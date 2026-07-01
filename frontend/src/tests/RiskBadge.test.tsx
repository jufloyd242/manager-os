import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RiskBadge } from '../components/RiskBadge'
import type { RiskLevel } from '../api/client'

describe('RiskBadge', () => {
  const cases: Array<[RiskLevel, string]> = [
    ['local_safe', 'Local Safe'],
    ['local_write', 'Local Write'],
    ['external_bounded', 'External Bounded'],
    ['external_high_risk', 'External High Risk'],
    ['blocked', 'Blocked'],
  ]

  it.each(cases)('renders correct label for %s', (level, expectedLabel) => {
    render(<RiskBadge level={level} />)
    expect(screen.getByText(expectedLabel)).toBeInTheDocument()
  })
})
