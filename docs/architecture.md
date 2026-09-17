# Milestone 3 Architecture

## Components

1. **Controller (Client)**: 
   - Submits jobs using `POST /jobs`.
   - Polls job status using `GET /jobs/{job_id}`.
2. **Backend**:
   - `JobManager`: Creates jobs, assigns unique IDs, stores them, and manages state transitions (queued -> assigned -> running -> completed/failed).
   - `Scheduler`: Automatically attempts to find an available worker (`status == "online"` and `current_job_id == None`) and assigns the job.
   - `Registry`: Keeps track of worker state and heartbeats.
3. **Worker**:
   - Registers with the backend on startup.
   - Accepts jobs on `POST /jobs/assign` (or rejects if busy).
   - (In future milestones) Executes jobs and reports status back to `POST /jobs/{job_id}/status`.

## Job State Machine

- `queued`: Initial state when submitted.
- `assigned`: Backend has assigned the job to a worker.
- `running`: Worker has started execution.
- `completed`: Worker finished successfully.
- `failed`: Worker encountered an error, or rejected assignment.

## Worker State
- `status`: `"online"` or `"offline"`.
- `current_job_id`: The ID of the currently assigned job, or `None` if idle.


