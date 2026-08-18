import { describe, expect, it } from 'vitest'
import { documentName, emptyToNull, formatFileSize, humanize } from './format'

describe('humanize', () => {
  it('turns backend tokens into readable labels', () => {
    expect(humanize('take_home')).toBe('Take Home')
    expect(humanize('applied')).toBe('Applied')
    expect(humanize('technical_interview')).toBe('Technical Interview')
  })

  it('keeps known acronyms and brand names correctly cased', () => {
    expect(humanize('hr_interview')).toBe('HR Interview')
    expect(humanize('cv')).toBe('CV')
    expect(humanize('linkedin')).toBe('LinkedIn')
  })

  it('handles missing values', () => {
    expect(humanize(null)).toBe('')
    expect(humanize(undefined)).toBe('')
    expect(humanize('')).toBe('')
  })
})

describe('emptyToNull', () => {
  it('converts blank input to null so nullable columns stay null', () => {
    expect(emptyToNull('')).toBeNull()
    expect(emptyToNull('   ')).toBeNull()
  })

  it('trims real values', () => {
    expect(emptyToNull('  Berlin ')).toBe('Berlin')
  })
})

describe('formatFileSize', () => {
  it('scales units', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(2048)).toBe('2.0 KB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('documentName', () => {
  it('prefers the human label', () => {
    expect(documentName({ id: 1, label: 'CV v3', original_filename: 'cv.pdf' })).toBe('CV v3')
  })

  it('falls back to the filename, then the id', () => {
    expect(documentName({ id: 1, label: null, original_filename: 'cv.pdf' })).toBe('cv.pdf')
    expect(documentName({ id: 7, label: null, original_filename: null })).toBe('Document 7')
  })
})
