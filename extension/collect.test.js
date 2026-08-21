import assert from 'node:assert/strict'
import { test } from 'node:test'

import { collectPageSnapshot } from './collect.js'

// `collectPageSnapshot` is serialised by chrome.scripting.executeScript and
// re-created inside the page, where this module does not exist. If it ever
// references a module-scope binding it will keep passing every test here and
// fail only in a real browser, with a confusing "x is not defined". These tests
// guard that specific hazard cheaply.

test('the injected collector takes no arguments', () => {
  // executeScript calls it with none; a required parameter would be undefined.
  assert.equal(collectPageSnapshot.length, 0)
})

test('the injected collector references no module-scope bindings', () => {
  const source = collectPageSnapshot.toString()

  // Anything imported or declared at module scope in collect.js / extract.js /
  // guess.js would be a free variable inside the page.
  const forbidden = [
    'extractJobPosting',
    'cleanDescription',
    'guessCompanyAndRole',
    'scoreDescriptionBlock',
    'JD_SECTION',
    'NAV_VOCAB',
  ]
  for (const name of forbidden) {
    assert.ok(!source.includes(name), `collector must not reference ${name}`)
  }

  // It must also not contain import/require, which cannot work when injected.
  assert.ok(!/\bimport\s|\brequire\(/.test(source), 'collector must not import anything')
})

test('the injected collector only uses globals available in a page', () => {
  const source = collectPageSnapshot.toString()
  // Sanity: it should be reading the DOM, not some abstraction we forgot to inline.
  assert.ok(source.includes('document.querySelectorAll'))
  assert.ok(source.includes('document.title'))
})
