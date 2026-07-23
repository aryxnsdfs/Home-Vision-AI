import test from "node:test";
import assert from "node:assert/strict";
import { isActiveValidatedResult } from "./src/store/jobResultGate.js";

test("failed job B cannot apply successful artifact from job A", () => {
  const jobA = { job_id: "job-a", success: true, validation_passed: true, blueprint_url: "/a.png" };
  const jobBFailure = { job_id: "job-b", success: false, validation_passed: false };
  assert.equal(isActiveValidatedResult(jobA, "job-b"), false);
  assert.equal(isActiveValidatedResult(jobBFailure, "job-b"), false);
});

test("only the active successful validated job is accepted", () => {
  const jobB = { job_id: "job-b", success: true, validation_passed: true };
  assert.equal(isActiveValidatedResult(jobB, "job-b"), true);
});
