import { useParams } from "react-router-dom";

export function JobDetails() {
  const { jobId } = useParams();
  // TODO: score breakdown, strengths/gaps, "should I apply?" — docs/matching-engine.md
  return <h1>Job {jobId}</h1>;
}
