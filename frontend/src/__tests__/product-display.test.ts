import { describe, expect, it } from 'vitest'
import {
  displayProductName,
  humanizeProductId,
  latestBlueprintIn,
  platformCardTitle,
} from '../productDisplay'

describe('displayProductName', () => {
  it('prefers blueprint.product_name over a generic product_id slug', () => {
    expect(
      displayProductName({ productName: 'FinanceOps', productId: 'product' }),
    ).toBe('FinanceOps')
  })

  it('trims product_name and ignores a blank name', () => {
    expect(
      displayProductName({ productName: '  Product Platform  ', productId: 'product' }),
    ).toBe('Product Platform')
    expect(displayProductName({ productName: '   ', productId: 'product' })).toBe('Product')
  })

  it('uses another honest design name when product_name is missing', () => {
    expect(
      displayProductName({ altName: 'FinanceOps Desk', productId: 'product' }),
    ).toBe('FinanceOps Desk')
  })

  it('humanizes a slug product_id when no name is known', () => {
    expect(displayProductName({ productId: 'residential-lettings' })).toBe(
      'Residential Lettings',
    )
    expect(displayProductName({ productId: 'veterinary_care' })).toBe('Veterinary Care')
  })

  it('falls back to Untitled platform when nothing is known', () => {
    expect(displayProductName({})).toBe('Untitled platform')
  })
})

describe('platformCardTitle', () => {
  it('matches the Platforms card contract from #436', () => {
    expect(platformCardTitle(undefined, 'product')).toBe('Product')
    expect(platformCardTitle('  ', 'residential-lettings')).toBe('Residential Lettings')
    expect(platformCardTitle('FinanceOps', 'product')).toBe('FinanceOps')
    expect(platformCardTitle(null, null)).toBe('')
  })
})

describe('humanizeProductId', () => {
  it('title-cases hyphen and underscore slugs', () => {
    expect(humanizeProductId('product')).toBe('Product')
    expect(humanizeProductId('finance-ops')).toBe('Finance Ops')
  })
})

describe('latestBlueprintIn', () => {
  type Msg = {
    role: string
    card?: string
    engine?: string
    blueprint?: { product_name?: string }
  }
  // bakery-operations: drafted on the Floor, approved, and titled
  // "Untitled platform" once the generation card became the newest card.
  const drafted: Msg = {
    role: 'factory',
    card: 'blueprint',
    blueprint: { product_name: 'Bakery Branch Operations Platform' },
  }
  const generation: Msg = { role: 'factory', card: 'generation', engine: 'runner' }

  it('finds the blueprint behind a newer generation card', () => {
    const user: Msg = { role: 'user' }
    expect(latestBlueprintIn([drafted, user, generation])).toEqual({
      product_name: 'Bakery Branch Operations Platform',
    })
  })

  it('prefers the newest blueprint when the brief was redrafted', () => {
    const redraft: Msg = { role: 'factory', card: 'blueprint', blueprint: { product_name: 'Bakery v2' } }
    expect(latestBlueprintIn([drafted, redraft, generation])?.product_name).toBe('Bakery v2')
  })

  it('titles a freshly approved build by its name, not Untitled platform', () => {
    const bp = latestBlueprintIn([drafted, generation])
    expect(displayProductName({ productName: bp?.product_name })).toBe(
      'Bakery Branch Operations Platform',
    )
  })

  it('returns undefined when nothing was drafted', () => {
    expect(latestBlueprintIn([generation])).toBeUndefined()
  })
})
