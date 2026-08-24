"""Email classification: the contract, the provider transports, and the guards.

Phase 6.2A-1 scope, deliberately narrow: turn one stored email into one
validated `EmailClassification`. Nothing here reads or writes the database,
matches an application, creates a Suggestion, changes a status, or is called
during Gmail sync. This slice exists to find out whether the classifier is good
enough to be worth integrating — production wiring is the next decision.

Layering, following `services/gmail.py`:

    EmailClassifier              a Protocol — all the rest of JobOps ever needs
      StructuredEmailClassifier  the domain logic: guard, validate, verify
        StructuredJsonCompleter    a Protocol — the one provider call
          OpenAIResponsesCompleter    OpenAI Responses + Structured Outputs
          AnthropicMessagesCompleter  Anthropic Messages + structured outputs

The two seams do different jobs. The outer one keeps provider objects out of
the application. The inner one is what makes the interesting logic — schema
handling, error translation, evidence grounding — testable exhaustively without
a network, an API key, or either SDK installed.

`StructuredEmailClassifier` carries no provider name because it contains no
provider code: identical validation, identical grounding, identical errors
whichever transport it is handed. Swapping providers is a configuration change
(`EMAIL_CLASSIFIER_PROVIDER`), which is the property that makes a fair
side-by-side eval possible at all.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.config import EmailClassifierProvider, settings
from app.core.classification_evidence import unverifiable_evidence
from app.core.classification_prompt import (
    CLASSIFICATION_SCHEMA_NAME,
    INSTRUCTIONS,
    render_email,
)
from app.core.errors import (
    EmailClassificationFailed,
    EmailClassificationNotGrounded,
    EmailClassifierNotConfigured,
    OutgoingMessageNotClassifiable,
)
from app.core.structured_schema import strict_schema_for
from app.enums import EmailDirection
from app.schemas.classification import EmailClassification

# The model each provider uses when `EMAIL_CLASSIFIER_MODEL` is unset.
#
# Per-provider rather than one shared default, because a single pinned string
# would be sent to whichever provider is selected — flip the provider and you
# would be asking Anthropic for an OpenAI model. Setting the variable
# explicitly still overrides, which is how a model comparison is run.
DEFAULT_MODELS: dict[EmailClassifierProvider, str] = {
    EmailClassifierProvider.OPENAI: "gpt-5.6-luna",
    EmailClassifierProvider.ANTHROPIC: "claude-opus-5",
}

# Which environment variable holds each provider's key. Used only to tell the
# user which one to set; the values themselves are never read for display.
PROVIDER_KEY_VARIABLES: dict[EmailClassifierProvider, str] = {
    EmailClassifierProvider.OPENAI: "OPENAI_API_KEY",
    EmailClassifierProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
}


def configured_api_key(provider: EmailClassifierProvider) -> str | None:
    """The configured key for one provider, or None. Never logged or returned
    anywhere user-visible — callers use it only to decide whether to proceed."""
    if provider is EmailClassifierProvider.OPENAI:
        return settings.openai_api_key
    return settings.anthropic_api_key


def resolved_model(provider: EmailClassifierProvider) -> str:
    """The model to use: the configured one, else the provider's default."""
    return settings.email_classifier_model or DEFAULT_MODELS[provider]


def _require_api_key(provider: EmailClassifierProvider) -> str:
    key = configured_api_key(provider)
    if not key:
        raise EmailClassifierNotConfigured(provider.value, PROVIDER_KEY_VARIABLES[provider])
    return key


@dataclass(frozen=True)
class ClassificationInput:
    """Everything the classifier needs about one email, and nothing else.

    Deliberately not an `EmailMessage`. Passing the ORM row would make it far
    too easy to send a Gmail id or a database id to a third party by accident;
    an explicit little struct makes the outbound surface something you have to
    choose, field by field.

    `direction` is carried but never sent: it decides *whether* to classify, not
    what the message means.
    """

    sender: str
    subject: str | None
    body_text: str | None
    received_at: datetime | None = None
    direction: EmailDirection = EmailDirection.INCOMING


@dataclass(frozen=True)
class StructuredCompletion:
    """One provider reply: the JSON text, and usage if the provider reported it."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class StructuredJsonCompleter(Protocol):
    """The single provider operation this classifier performs.

    Narrow on purpose: instructions in, JSON text out. Everything provider-shaped
    stops here, which is what lets the classifier's own behaviour be tested with
    a fake that is a dozen lines long.
    """

    def complete_json(
        self, *, instructions: str, user_content: str, schema: dict[str, Any], schema_name: str
    ) -> StructuredCompletion: ...


class EmailClassifier(Protocol):
    """What the rest of JobOps depends on. No provider types appear here."""

    def classify(self, message: ClassificationInput) -> EmailClassification: ...


class OpenAIResponsesCompleter:
    """The only OpenAI-aware class in the project.

    Uses the Responses API with Structured Outputs, so the reply is constrained
    to the schema by the provider rather than coaxed into JSON by the prompt and
    parsed hopefully at this end. No tools, no function calling, no retrieval —
    this is a bounded text-classification call.

    Every SDK exception is translated into a domain error. That is the whole
    reason this class exists: nothing above it should have to import `openai` to
    handle a failure, and nothing below it should leak upward.
    """

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls) -> "OpenAIResponsesCompleter":
        """Build a completer from configuration.

        The SDK is imported lazily, matching `GmailClient.from_stored_credentials`:
        nothing that merely imports this module needs `openai` installed, so the
        unit tests do not need it either.
        """
        api_key = _require_api_key(EmailClassifierProvider.OPENAI)

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise EmailClassificationFailed(
                "the 'openai' package is not installed in this environment"
            ) from exc

        client = OpenAI(
            api_key=api_key,
            timeout=settings.email_classifier_timeout_seconds,
            # One attempt beyond the first. The project has no retry convention
            # yet, and a classification is not worth hammering a provider over:
            # a failure here is reported, not fought.
            max_retries=1,
        )
        return cls(client, resolved_model(EmailClassifierProvider.OPENAI))

    def complete_json(
        self, *, instructions: str, user_content: str, schema: dict[str, Any], schema_name: str
    ) -> StructuredCompletion:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=[{"role": "user", "content": user_content}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
        except Exception as exc:
            # Includes connection errors, timeouts, rate limits and auth
            # failures. `type(exc).__name__` rather than the object keeps a
            # provider class out of the domain error, and `str(exc)` never
            # contains the key — the SDK does not echo it.
            raise EmailClassificationFailed(f"{type(exc).__name__}: {exc}") from exc

        return self._read_response(response)

    @staticmethod
    def _read_response(response: Any) -> StructuredCompletion:
        """Extract JSON text from a Responses reply, refusing anything partial.

        A truncated or refused response can still carry text that happens to
        parse. Treating either as a result would turn "the model declined" into
        a confident-looking classification, so both are failures.
        """
        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            raise EmailClassificationFailed(f"response incomplete ({reason})")

        refusal = _find_refusal(response)
        if refusal:
            raise EmailClassificationFailed(f"model refused to answer: {refusal}")

        text = getattr(response, "output_text", None)
        if not text or not str(text).strip():
            raise EmailClassificationFailed("response contained no output text")

        usage = getattr(response, "usage", None)
        return StructuredCompletion(
            text=str(text),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def _find_refusal(response: Any) -> str | None:
    """The refusal text, if the model declined rather than answered."""
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return str(getattr(part, "refusal", "") or "refused")
    return None


class AnthropicMessagesCompleter:
    """The only Anthropic-aware class in the project.

    Uses the Messages API with structured outputs (`output_config.format`), so
    the reply is constrained to the schema by the provider rather than coaxed
    into JSON by the prompt. No tools, no agent loop — a bounded classification.

    The schema handed over is the same one the OpenAI path uses, derived from
    `EmailClassification` by `core/structured_schema.py`. That was not a given:
    the hardening done for OpenAI's strict mode — every property required,
    `additionalProperties: false` throughout — happens to be exactly what
    Anthropic's structured outputs ask for too, so no provider-specific schema
    variant was needed.

    Every SDK exception is translated into a domain error, for the same reason
    as the OpenAI transport: nothing above this class should import `anthropic`
    to handle a failure.
    """

    def __init__(self, client: Any, model: str, max_output_tokens: int) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    @classmethod
    def from_settings(cls) -> "AnthropicMessagesCompleter":
        api_key = _require_api_key(EmailClassifierProvider.ANTHROPIC)

        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise EmailClassificationFailed(
                "the 'anthropic' package is not installed in this environment"
            ) from exc

        client = Anthropic(
            api_key=api_key,
            timeout=settings.email_classifier_timeout_seconds,
            max_retries=1,
        )
        return cls(
            client,
            resolved_model(EmailClassifierProvider.ANTHROPIC),
            settings.email_classifier_max_output_tokens,
        )

    def complete_json(
        self, *, instructions: str, user_content: str, schema: dict[str, Any], schema_name: str
    ) -> StructuredCompletion:
        # `schema_name` is deliberately unused: Anthropic's `output_config`
        # format takes the schema alone, where OpenAI's requires a name and an
        # explicit `strict` flag. The protocol carries what the widest
        # implementation needs; a transport ignoring a field it has no use for
        # is cheaper than two protocols.
        try:
            response = self._client.messages.create(
                model=self._model,
                # Required by this API, unlike OpenAI Responses. On models where
                # thinking is on by default, reasoning tokens are drawn from
                # this same budget — hence a generous default.
                max_tokens=self._max_output_tokens,
                # The system prompt is the instructions channel here, keeping
                # the untrusted email in the user turn exactly as before.
                system=instructions,
                messages=[{"role": "user", "content": user_content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as exc:
            raise EmailClassificationFailed(f"{type(exc).__name__}: {exc}") from exc

        return self._read_response(response)

    @staticmethod
    def _read_response(response: Any) -> StructuredCompletion:
        """Extract JSON text from a Messages reply, refusing anything partial.

        Same principle as the OpenAI transport: a truncated or refused response
        can still carry text that happens to parse, and treating either as a
        result would turn "the model stopped" into a confident-looking answer.
        """
        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise EmailClassificationFailed(f"model refused to answer ({category})")

        if stop_reason == "max_tokens":
            # The answer was cut off mid-JSON. Reported rather than parsed,
            # since a partial object is not a partial classification.
            raise EmailClassificationFailed(
                "response truncated at max_tokens — raise "
                "EMAIL_CLASSIFIER_MAX_OUTPUT_TOKENS"
            )

        text = next(
            (
                block.text
                for block in getattr(response, "content", None) or []
                if getattr(block, "type", None) == "text"
            ),
            None,
        )
        if not text or not str(text).strip():
            raise EmailClassificationFailed("response contained no output text")

        usage = getattr(response, "usage", None)
        return StructuredCompletion(
            text=str(text),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def build_completer(
    provider: EmailClassifierProvider | None = None,
) -> StructuredJsonCompleter:
    """The configured provider's transport.

    The single place that maps a provider name to an implementation. Everything
    above it — validation, grounding, error handling, the eval runner's metrics
    — is identical whichever branch is taken.
    """
    selected = provider or settings.email_classifier_provider
    if selected is EmailClassifierProvider.ANTHROPIC:
        return AnthropicMessagesCompleter.from_settings()
    return OpenAIResponsesCompleter.from_settings()


class StructuredEmailClassifier:
    """Classify one email, and refuse to return anything it cannot stand behind.

    Provider-neutral by construction: it holds a `StructuredJsonCompleter` and
    contains no provider code at all, which is why it is not named after one.
    An OpenAI reply and an Anthropic reply pass through exactly the same
    validation, the same grounding check, and produce exactly the same domain
    errors — the property that makes comparing the two a measurement rather
    than an apples-to-oranges guess.

    The provider constrains the reply to the schema; this class decides whether
    the reply is acceptable, which is a different and stricter question. Three
    gates, in order:

      1. Direction — an outgoing message is rejected before any request is made,
         so the mistake is free.
      2. The contract — the reply is validated through `EmailClassification`,
         which enforces everything the wire schema deliberately does not:
         timezone-aware datetimes, evidence bounds, the evidence requirement,
         and the absence of action-policy fields.
      3. Grounding — every excerpt must appear in the text that was actually
         sent. A schema-valid answer with invented support is rejected whole,
         never quietly repaired.
    """

    def __init__(
        self,
        completer: StructuredJsonCompleter,
        *,
        max_body_chars: int | None = None,
    ) -> None:
        self._completer = completer
        self._max_body_chars = (
            max_body_chars
            if max_body_chars is not None
            else settings.email_classifier_max_body_chars
        )
        # Derived once from the contract, not hand-written. See
        # `core/structured_schema.py`.
        self._schema = strict_schema_for(EmailClassification)
        self.last_usage: StructuredCompletion | None = None

    @classmethod
    def from_settings(
        cls, provider: EmailClassifierProvider | None = None
    ) -> "StructuredEmailClassifier":
        """Build a classifier for the configured provider.

        `provider` overrides configuration for one call, which is what lets the
        eval runner compare providers in a single sitting without editing .env.
        """
        return cls(build_completer(provider))

    def classify(self, message: ClassificationInput) -> EmailClassification:
        if message.direction == EmailDirection.OUTGOING:
            # Before the request, not after: policy says we do not classify the
            # user's own writing, and an API call to learn that would be waste.
            raise OutgoingMessageNotClassifiable()

        user_content = render_email(
            sender=message.sender,
            subject=message.subject,
            body_text=message.body_text,
            received_at=message.received_at,
            max_body_chars=self._max_body_chars,
        )

        completion = self._completer.complete_json(
            instructions=INSTRUCTIONS,
            user_content=user_content,
            schema=self._schema,
            schema_name=CLASSIFICATION_SCHEMA_NAME,
        )
        self.last_usage = completion

        try:
            classification = EmailClassification.model_validate_json(completion.text)
        except ValidationError as exc:
            # Structured outputs make this unlikely but not impossible: the
            # contract enforces rules the wire schema cannot express, so a
            # schema-valid reply can still be contract-invalid (a naive
            # datetime, missing evidence). Reported as a failure, never
            # downgraded to a guess.
            raise EmailClassificationFailed(
                f"response did not satisfy the contract: {exc}"
            ) from exc
        except ValueError as exc:
            raise EmailClassificationFailed(f"response was not valid JSON: {exc}") from exc

        # Grounded against exactly the text the model saw — including the
        # truncation the renderer may have applied. Checking against the full
        # stored body would be more permissive than what was actually shown.
        ungrounded = unverifiable_evidence(
            classification,
            subject=message.subject,
            body_text=_visible_body(message.body_text, self._max_body_chars),
        )
        if ungrounded:
            raise EmailClassificationNotGrounded(ungrounded)

        return classification


def _visible_body(body_text: str | None, max_body_chars: int) -> str | None:
    """The portion of the body the model was actually shown."""
    if body_text is None:
        return None
    return body_text[:max_body_chars]
