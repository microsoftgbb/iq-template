// The sample questions for the template's fictional sample: the live ScenarioOntology (Pump B is in maintenance at
// North Works), the starter document, public context about the asset type, and the user's own mail.
export interface Sample {
  question: string
  // Shown next to a sample that needs an IQ a deployment may leave off.
  note?: string
}

export const SAMPLES: readonly Sample[] = [
  { question: 'Which assets are in maintenance, and at which site?' },
  { question: 'What does the starter document say about where current entity values come from?' },
  { question: 'What general public guidance exists for centrifugal pump maintenance?' },
  { question: 'Find recent mail about Pump B', note: 'needs Work IQ' },
]
