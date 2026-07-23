export const isActiveValidatedResult = (result, activeJobId) => Boolean(
  activeJobId
  && String(result?.job_id || "") === String(activeJobId)
  && result?.success === true
  && result?.validation_passed === true
);
