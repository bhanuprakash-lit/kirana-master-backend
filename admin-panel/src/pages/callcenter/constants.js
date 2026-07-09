// Shared enums + human labels for the call-center UI (must match the backend's
// callcenter.repository VALID_* sets).

export const DISPOSITIONS = [
  { value: 'answered', label: 'Answered' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'busy', label: 'Busy' },
  { value: 'switched_off', label: 'Switched off' },
  { value: 'wrong_number', label: 'Wrong number' },
  { value: 'invalid_number', label: 'Invalid number' },
];

export const USAGE = [
  { value: 'using_active', label: 'Using actively' },
  { value: 'using_rare', label: 'Using rarely' },
  { value: 'stopped', label: 'Stopped using' },
  { value: 'never_started', label: 'Never started' },
  { value: 'needs_training', label: 'Needs training' },
];

export const SENTIMENTS = [
  { value: 'positive', label: '🙂 Positive' },
  { value: 'neutral', label: '😐 Neutral' },
  { value: 'negative', label: '☹️ Negative' },
];

export const NEXT_ACTIONS = [
  { value: 'done', label: 'Done' },
  { value: 'callback', label: 'Schedule callback' },
  { value: 'escalate', label: 'Escalate' },
  { value: 'do_not_call', label: 'Do not call' },
];

export const TAGS = [
  { value: 'bug', label: 'Bug' },
  { value: 'feature_request', label: 'Feature request' },
  { value: 'pricing', label: 'Pricing' },
  { value: 'training', label: 'Training' },
  { value: 'happy', label: 'Happy' },
  { value: 'churn_risk', label: 'Churn risk' },
];

export const labelOf = (list, value) =>
  (list.find((o) => o.value === value) || {}).label || value || '—';
