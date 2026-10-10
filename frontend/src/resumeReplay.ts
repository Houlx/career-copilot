// Reviewed synthetic input; no user data.
export const resumeReplay = {
  job: {
    title: 'Synthetic application engineer',
    raw_jd: 'Python required. React and TypeScript preferred. Kubernetes required.',
    coverage: 50.0,
  },
  requirements: [
    {
      id: 'req-001',
      text: 'Python',
      importance: 'required',
      category: 'skill',
      scoreability: 'SCORED',
    },
    {
      id: 'req-002',
      text: 'React and TypeScript',
      importance: 'preferred',
      category: 'skill',
      scoreability: 'SCORED',
    },
    {
      id: 'req-003',
      text: 'Kubernetes',
      importance: 'required',
      category: 'skill',
      scoreability: 'SCORED',
    },
  ],
  matches: [
    {
      requirement_id: 'req-001',
      status: 'MATCH',
      evidence_ids: [
        'career-work-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      ],
      explanation: 'Python services supported.',
    },
    {
      requirement_id: 'req-002',
      status: 'PARTIAL',
      evidence_ids: [
        'career-project-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      ],
      explanation: 'React prototype only; no TypeScript evidence.',
    },
    {
      requirement_id: 'req-003',
      status: 'GAP',
      evidence_ids: [],
      explanation: 'No Kubernetes evidence.',
    },
  ],
  evidence: [
    {
      id: 'career-work-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      type: 'work',
      title: 'Synthetic Python engineer',
      normalized_content:
        'Worked as a Python engineer at Synthetic Studio from 2022 to 2024. Built Python 3.12 services and reduced latency by 20%.',
    },
    {
      id: 'career-project-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      type: 'project',
      title: 'Synthetic React prototype',
      normalized_content: 'Built a React prototype for an internal tool.',
    },
  ],
  requested_language: 'en',
};
