"""
Sample Workload: CPU Stress Test
Performs intensive mathematical calculations to prove remote resource utilization.
"""

import argparse
import hashlib
import time


def cpu_stress(duration_seconds: int):
    print(f"Starting CPU stress workload for {duration_seconds} seconds...")
    start_time = time.time()
    iterations = 0

    while time.time() - start_time < duration_seconds:
        # Compute SHA-256 hashes in a loop to generate CPU load
        data = f"payload-load-{iterations}".encode("utf-8")
        _ = hashlib.sha256(data).hexdigest()
        iterations += 1

    elapsed = time.time() - start_time
    print(f"Computation completed successfully.")
    print(f"Total iterations : {iterations:,}")
    print(f"Elapsed time     : {elapsed:.2f} seconds")
    print(f"Throughput       : {iterations / elapsed:,.0f} hashes/sec")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPU benchmark workload")
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Duration to run CPU benchmark in seconds",
    )
    args = parser.parse_args()
    cpu_stress(args.duration)
