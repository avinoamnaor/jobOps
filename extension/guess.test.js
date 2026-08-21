import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  guessApplicationChannel,
  guessCompanyAndRole,
  guessLocationAndWorkMode,
  stripCompanyNoise,
} from './guess.js'

test('Role - Company Careers (the Harmonic case)', () => {
  const { role, company } = guessCompanyAndRole({
    title: 'Junior SW Development Engineer - Harmonic Inc. Careers',
  })
  assert.equal(role, 'Junior SW Development Engineer')
  assert.equal(company, 'Harmonic Inc.')
})

test('Role | Company', () => {
  const g = guessCompanyAndRole({ title: 'Fullstack Developer | Acme' })
  assert.equal(g.role, 'Fullstack Developer')
  assert.equal(g.company, 'Acme')
})

test('Role @ Company', () => {
  const g = guessCompanyAndRole({ title: 'Software Engineer @ BluePeak Systems' })
  assert.equal(g.role, 'Software Engineer')
  assert.equal(g.company, 'BluePeak Systems')
})

test('Role at Company', () => {
  const g = guessCompanyAndRole({ title: 'Backend Engineer at Meridian AI' })
  assert.equal(g.role, 'Backend Engineer')
  assert.equal(g.company, 'Meridian AI')
})

test('Company - Role orientation is decided by which segment is a job title', () => {
  const g = guessCompanyAndRole({ title: 'Acme - Senior Data Scientist' })
  assert.equal(g.role, 'Senior Data Scientist')
  assert.equal(g.company, 'Acme')
})

test('a marketing heading is ignored when a job-title heading exists', () => {
  const g = guessCompanyAndRole({
    title: '',
    headings: ['Join the Harmonic adventure', 'Junior SW Development Engineer'],
  })
  assert.equal(g.role, 'Junior SW Development Engineer')
})

test('poor confidence yields a blank role rather than a marketing guess', () => {
  const g = guessCompanyAndRole({
    title: 'Join the Harmonic adventure',
    headings: ['Join the Harmonic adventure'],
  })
  assert.equal(g.role, '')
})

test('stripCompanyNoise removes generic trailing noise but keeps "Inc."', () => {
  assert.equal(stripCompanyNoise('Harmonic Inc. Careers'), 'Harmonic Inc.')
  assert.equal(stripCompanyNoise('Acme Jobs'), 'Acme')
  assert.equal(stripCompanyNoise('Acme Career Site'), 'Acme')
  assert.equal(stripCompanyNoise('Acme Job Application'), 'Acme')
})

test('location + work mode from a header line (the Harmonic case)', () => {
  const g = guessLocationAndWorkMode({ text: 'Caesarea, Israel (Hybrid)\nAbout the role…' })
  assert.equal(g.location, 'Caesarea, Israel')
  assert.equal(g.work_mode, 'Hybrid')
})

test('work mode separated by a dash, and on-site normalization', () => {
  assert.deepEqual(guessLocationAndWorkMode({ headings: ['Tel Aviv, Israel - Remote'] }), {
    location: 'Tel Aviv, Israel',
    work_mode: 'Remote',
  })
  assert.deepEqual(guessLocationAndWorkMode({ headings: ['Berlin, Germany • Onsite'] }), {
    location: 'Berlin, Germany',
    work_mode: 'On-site',
  })
})

test('no location signal yields blanks rather than a bad guess', () => {
  const g = guessLocationAndWorkMode({ text: 'We build great software for teams.' })
  assert.equal(g.location, '')
  assert.equal(g.work_mode, '')
})

test('application channel is classified from the URL/domain only', () => {
  assert.equal(guessApplicationChannel('https://www.linkedin.com/jobs/view/123'), 'linkedin')
  assert.equal(guessApplicationChannel('https://www.indeed.com/viewjob?jk=abc'), 'job_board')
  assert.equal(guessApplicationChannel('https://boards.greenhouse.io/acme/jobs/1'), 'company_site')
  assert.equal(guessApplicationChannel('https://careers.harmonicinc.com/o/engineer'), 'company_site')
  assert.equal(guessApplicationChannel('not a url'), 'company_site')
})
