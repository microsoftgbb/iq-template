// The sample questions: the demo script's questions (demo/script.json), about the template's fictional sample: the
// live ScenarioOntology (Pump B is in maintenance at North Works), the starter document, public context about the
// asset type, and the user's own mail.
import { SCRIPT, WORK_IQ } from './demo/script.ts'

export interface Sample {
  question: string
  // Shown next to a sample that needs an IQ a deployment may leave off.
  note?: string
}

export const SAMPLES: readonly Sample[] = SCRIPT.steps.flatMap((step) => (step.question
  ? [{ question: step.question, ...(step.requires === WORK_IQ ? { note: 'needs Work IQ' } : {}) }]
  : []))
