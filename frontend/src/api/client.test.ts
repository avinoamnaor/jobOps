import { describe, expect, it } from 'vitest'
import { buildQuery, toApiError } from './client'

/** Build a Response-like object without needing a real network. */
function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    json: async () => {
      if (body === undefined) throw new Error('no body')
      return body
    },
  } as Response
}

describe('toApiError', () => {
  it('reads the plain string detail our own handlers return', async () => {
    const error = await toApiError(
      fakeResponse(404, { detail: 'Application 5 does not exist' }),
    )

    expect(error.status).toBe(404)
    expect(error.message).toBe('Application 5 does not exist')
    expect(error.fieldErrors).toEqual([])
  })

  it('turns a Pydantic validation list into per-field messages', async () => {
    const error = await toApiError(
      fakeResponse(422, {
        detail: [
          { loc: ['body', 'company_name'], msg: 'Field required' },
          { loc: ['body', 'role_title'], msg: 'Field required' },
        ],
      }),
    )

    expect(error.status).toBe(422)
    expect(error.fieldErrors).toEqual([
      { field: 'company_name', message: 'Field required' },
      { field: 'role_title', message: 'Field required' },
    ])
    expect(error.message).toBe('2 fields need attention.')
  })

  it('names the single offending field when there is only one', async () => {
    const error = await toApiError(
      fakeResponse(422, {
        detail: [{ loc: ['body', 'status'], msg: 'Extra inputs are not permitted' }],
      }),
    )

    expect(error.message).toBe('status: Extra inputs are not permitted')
  })

  it('falls back to a readable message when the body is not JSON', async () => {
    const error = await toApiError(fakeResponse(500, undefined))

    expect(error.status).toBe(500)
    expect(error.message).toContain('server ran into a problem')
  })

  it('has a sensible message for a conflict', async () => {
    const error = await toApiError(fakeResponse(409, {}))

    expect(error.message).toContain('conflicts with the current state')
  })
})

describe('buildQuery', () => {
  it('omits empty values so we never send ?q=', () => {
    expect(buildQuery({ q: '', status: 'applied', page: 1 })).toBe('?status=applied&page=1')
  })

  it('returns an empty string when nothing is set', () => {
    expect(buildQuery({ q: '', status: undefined, channel: null })).toBe('')
  })

  it('encodes values', () => {
    expect(buildQuery({ q: 'a&b c' })).toBe('?q=a%26b+c')
  })
})
