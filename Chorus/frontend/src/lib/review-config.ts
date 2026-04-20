export type ReviewTemplateId = 'rfc' | 'launch' | 'architecture' | 'risk'
export type ReviewModeId = 'quick' | 'decision' | 'audit'

export interface ReviewTemplateDefinition {
  id: ReviewTemplateId
  label: string
  shortLabel: string
  summary: string
  promptSeed: string
  placeholder: string
  reportFocus: string
}

export interface ReviewModeDefinition {
  id: ReviewModeId
  label: string
  reviewers: number
  rounds: number
  summary: string
  deliverable: string
}

export const REVIEW_TEMPLATES: ReviewTemplateDefinition[] = [
  {
    id: 'rfc',
    label: 'RFC Review',
    shortLabel: 'RFC',
    summary: 'Pressure-test assumptions, tradeoffs, and rollout criteria before approval.',
    promptSeed:
      'Review this RFC. Summarize the strongest case for approval, the strongest objections, missing evidence, rollout risks, and the decision you would recommend.',
    placeholder:
      'Paste the RFC, ADR, or product spec. Include the proposal, alternatives considered, rollout plan, and open questions.',
    reportFocus: 'approval criteria, missing evidence, and decision readiness',
  },
  {
    id: 'launch',
    label: 'Launch Review',
    shortLabel: 'Launch',
    summary: 'Check launch readiness, dependencies, metrics, and rollback planning.',
    promptSeed:
      'Review this launch plan. Surface readiness gaps, operational risks, measurement blind spots, and what must be true before launch.',
    placeholder:
      'Paste the launch brief, GTM checklist, or release plan. Include launch goals, dependencies, owners, metrics, and rollback options.',
    reportFocus: 'launch blockers, operational readiness, and rollback confidence',
  },
  {
    id: 'architecture',
    label: 'Architecture Review',
    shortLabel: 'Architecture',
    summary: 'Compare tradeoffs, failure modes, and implementation complexity.',
    promptSeed:
      'Review this architecture proposal. Compare tradeoffs, identify hidden constraints, highlight failure modes, and recommend the most defensible path.',
    placeholder:
      'Paste the architecture proposal. Include context, target state, alternatives, constraints, non-goals, and migration considerations.',
    reportFocus: 'tradeoffs, failure modes, and implementation risk',
  },
  {
    id: 'risk',
    label: 'Risk Review',
    shortLabel: 'Risk',
    summary: 'Expose downside scenarios, policy gaps, and mitigation priorities.',
    promptSeed:
      'Review this plan through a risk lens. Identify material failure modes, compliance or policy concerns, mitigations, and what requires escalation.',
    placeholder:
      'Paste the policy draft, incident review, migration plan, or decision memo. Include known constraints, downside scenarios, and current mitigations.',
    reportFocus: 'failure modes, mitigation gaps, and escalation points',
  },
]

export const REVIEW_MODES: ReviewModeDefinition[] = [
  {
    id: 'quick',
    label: 'Quick',
    reviewers: 3,
    rounds: 2,
    summary: 'Fast directional read for early drafts and triage.',
    deliverable: 'snapshot summary with the clearest risks and recommended next move',
  },
  {
    id: 'decision',
    label: 'Decision',
    reviewers: 5,
    rounds: 3,
    summary: 'Balanced review for plans that need a clear recommendation.',
    deliverable: 'decision memo with support, dissent, and a synthesized verdict',
  },
  {
    id: 'audit',
    label: 'Audit',
    reviewers: 7,
    rounds: 4,
    summary: 'Deep review for high-stakes launches, incidents, and architecture changes.',
    deliverable: 'audit-style report with blind spots, failure modes, and escalation advice',
  },
]

export function getReviewTemplate(templateId: ReviewTemplateId): ReviewTemplateDefinition {
  return REVIEW_TEMPLATES.find((template) => template.id === templateId) ?? REVIEW_TEMPLATES[0]
}

export function getReviewMode(modeId: ReviewModeId): ReviewModeDefinition {
  return REVIEW_MODES.find((mode) => mode.id === modeId) ?? REVIEW_MODES[1]
}
