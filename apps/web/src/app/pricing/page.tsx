import type { Metadata } from 'next'
import PricingPage from '@/app/_components/PricingPage'

export const metadata: Metadata = {
  title: 'Pricing — OpsLens AI',
  description: 'Simple flat pricing. AI included. No per-event overages. No surprise bills.',
}

export default function Page() {
  return <PricingPage />
}
