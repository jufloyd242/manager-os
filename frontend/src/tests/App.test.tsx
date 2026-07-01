import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'
import { mockDailyOperatingLoop, mockSystemStatus } from '../api/mockData'

describe('App', () => {
  it('renders the Command Tower heading', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Command Tower' })).toBeInTheDocument()
  })

  it('renders at least one system status card', async () => {
    render(<App />)
    expect(await screen.findByText(mockSystemStatus[0].label)).toBeInTheDocument()
  })

  it('renders at least one recommended action title from mock data', async () => {
    render(<App />)
    expect(
      await screen.findByText(mockDailyOperatingLoop.recommended_actions[0].title),
    ).toBeInTheDocument()
  })
})
