#!/usr/bin/env python3
"""
DUDE Latency Measurement System

Real-time performance measurement for DUDE's actual execution path.
Uses time.perf_counter_ns() for accurate timing without simulated delays.
"""

import time
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar

# ContextVar for request context isolation
_RequestContext = Dict[str, Any]
_current_request: ContextVar[Optional[_RequestContext]] = ContextVar(
    "dude_latency_request",
    default=None,
)

class DUDELatencyTracker:
    """Real-time latency tracking for DUDE execution."""

    def __init__(self) -> None:
        self.measurements: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.session_start = time.perf_counter_ns()

    def start_request(
        self,
        request_id: Optional[str] = None,
        description: str = "",
    ) -> str:
        start_ns = time.perf_counter_ns()

        if request_id is None:
            request_id = f"req_{start_ns // 1_000_000}"

        context: _RequestContext = {
            "request_id": request_id,
            "start_ns": start_ns,
            "active_stages": {},
        }
        _current_request.set(context)

        measurement = {
            "request_id": request_id,
            "description": description,
            "start_time_ns": start_ns,
            "timestamp": datetime.now().isoformat(),
            "stages": {},
            "success": True,
        }

        with self.lock:
            self.measurements.append(measurement)
        return request_id

    def _context(self) -> Optional[_RequestContext]:
        return _current_request.get()

    def _measurement_for(
        self,
        request_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            for measurement in reversed(self.measurements):
                if measurement["request_id"] == request_id:
                    return measurement
        return None

    def start_stage(self, stage_name: str) -> Optional[int]:
        context = self._context()
        if context is None:
            return None
        start_ns = time.perf_counter_ns()
        context["active_stages"][stage_name] = {
            "start_ns": start_ns,
        }
        measurement = self._measurement_for(context["request_id"])
        if measurement is not None:
            with self.lock:
                measurement["stages"][stage_name] = {
                    "start_ns": start_ns,
                }
        return start_ns

    def end_stage(
        self,
        stage_name: str,
        end_ns: Optional[int] = None,
    ) -> Optional[int]:
        context = self._context()
        if context is None:
            return None
        stage = context["active_stages"].pop(stage_name, None)
        if stage is None:
            return None
        if end_ns is None:
            end_ns = time.perf_counter_ns()
        duration_ns = end_ns - stage["start_ns"]
        measurement = self._measurement_for(context["request_id"])
        if measurement is not None:
            with self.lock:
                measurement_stage = measurement["stages"].get(stage_name)
                if measurement_stage is not None:
                    measurement_stage["end_ns"] = end_ns
                    measurement_stage["duration_ns"] = duration_ns
        return duration_ns

    def mark_failed(self) -> None:
        context = self._context()
        if context is None:
            return
        measurement = self._measurement_for(context["request_id"])
        if measurement is not None:
            with self.lock:
                measurement["success"] = False

    def end_request(
        self,
        request_id: Optional[str] = None,
        success: Optional[bool] = None,
    ) -> Optional[int]:
        context = self._context()
        if context is None:
            return None
        active_request_id = context["request_id"]
        if (
            request_id is not None
            and request_id != active_request_id
        ):
            return None
        # Close any remaining active stages before closing request.
        for stage_name in list(context["active_stages"].keys()):
            self.end_stage(stage_name)
        end_ns = time.perf_counter_ns()
        measurement = self._measurement_for(active_request_id)
        if measurement is not None:
            with self.lock:
                measurement["end_time_ns"] = end_ns
                measurement["total_duration_ns"] = (
                    end_ns - measurement["start_time_ns"]
                )
                if success is not None:
                    measurement["success"] = success
        _current_request.set(None)
        if measurement is not None:
            return measurement["total_duration_ns"]
        return None

    def get_current_request_id(self) -> Optional[str]:
        context = self._context()
        if context is None:
            return None
        return context["request_id"]

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            if not self.measurements:
                return None
            return dict(self.measurements[-1])

    def get_measurements(self) -> List[Dict[str, Any]]:
        """Get all measurements for report generation."""
        with self.lock:
            return [dict(m) for m in self.measurements]

# Global instance
_latency_tracker_instance = DUDELatencyTracker()

def get_latency_tracker() -> DUDELatencyTracker:
    """Get the global latency tracker instance."""
    return _latency_tracker_instance

@contextmanager
def measure_request(request_id: str = None, description: str = ""):
    """Context manager for measuring a complete request."""
    tracker = get_latency_tracker()
    req_id = tracker.start_request(request_id, description)
    try:
        yield req_id
    finally:
        tracker.end_request(req_id)

@contextmanager
def measure_stage(stage_name: str):
    """Context manager for measuring a specific stage."""
    tracker = get_latency_tracker()
    start_ns = tracker.start_stage(stage_name)
    try:
        yield start_ns
    finally:
        tracker.end_stage(stage_name)

# Integration helper for DUDE
class DUDELatencyInstrumentation:
    """Helper class to integrate latency tracking into DUDE components."""

    @staticmethod
    def wrap_brain_chat(brain_method):
        """Wrap brain.chat() with latency tracking."""
        def wrapped_chat(user_text, on_delta=None, on_tool=None, system_extra=""):
            from latency_measurement import get_latency_tracker
            tracker = get_latency_tracker()

            # Start request tracking
            req_id = tracker.start_request(
                description=f"brain.chat: {user_text[:50]}..."
            )

            # Stage timing
            tracker.start_stage('brain_entry')

            try:
                # Execute original method
                tracker.start_stage('model_processing')
                result = brain_method(user_text, on_delta, on_tool, system_extra)
                tracker.end_stage('model_processing')

                tracker.end_stage('brain_entry')
                return result

            except Exception as e:
                tracker.end_stage('brain_entry')
                tracker.end_request(req_id)
                raise e

        return wrapped_chat

    @staticmethod
    def wrap_tool_execution(tool_method):
        """Wrap tool execution with latency tracking."""
        def wrapped_tool(*args, **kwargs):
            from latency_measurement import get_latency_tracker
            tracker = get_latency_tracker()

            if tracker.current_request_id:
                # Get tool name from function
                tool_name = tool_method.__name__

                tracker.start_stage('tool_execution')
                try:
                    result = tool_method(*args, **kwargs)
                    tracker.end_stage('tool_execution')
                    return result
                except Exception as e:
                    tracker.end_stage('tool_execution')
                    raise e

            return tool_method(*args, **kwargs)

        return wrapped_tool

    @staticmethod
    def wrap_recovery_engine(recovery_method):
        """Wrap recovery engine with latency tracking."""
        def wrapped_recovery(*args, **kwargs):
            from latency_measurement import get_latency_tracker
            tracker = get_latency_tracker()

            if tracker.current_request_id:
                tracker.start_stage('recovery_processing')
                try:
                    result = recovery_method(*args, **kwargs)
                    tracker.end_stage('recovery_processing')
                    return result
                except Exception as e:
                    tracker.end_stage('recovery_processing')
                    raise e

            return recovery_method(*args, **kwargs)

        return wrapped_recovery

# Generate report function
def generate_latency_report():
    """Generate a comprehensive latency report."""
    tracker = get_latency_tracker()
    measurements = tracker.get_measurements()

    if not measurements:
        return "No measurements collected"

    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("DUDE LATENCY MEASUREMENT REPORT")
    report_lines.append("=" * 80)

    # Session summary
    total_requests = len(measurements)
    successful_requests = sum(1 for m in measurements if m['success'])
    session_duration_ns = max((m.get('end_time_ns', 0) for m in measurements), default=0) - tracker.session_start

    report_lines.append(f"\nSESSION SUMMARY:")
    report_lines.append(f"  Total Requests: {total_requests}")
    report_lines.append(f"  Successful Requests: {successful_requests}")
    report_lines.append(f"  Session Duration: {session_duration_ns / 1e9:.3f}s")

    # Performance by request type
    request_types = {}
    for measurement in measurements:
        req_type = measurement['description']
        if req_type not in request_types:
            request_types[req_type] = []
        request_types[req_type].append(measurement)

    report_lines.append(f"\nPERFORMANCE BY REQUEST TYPE:")
    for req_type, reqs in request_types.items():
        durations = [r.get('total_duration_ns', 0) / 1e6 for r in reqs if r['success']]
        if durations:
            avg_duration = sum(durations) / len(durations)
            report_lines.append(f"  {req_type}: {len(reqs)} requests, avg {avg_duration:.2f}ms")

    # Stage-wise performance
    all_stages = {}
    for measurement in measurements:
        if measurement['success']:
            for stage_name, stage_data in measurement['stages'].items():
                if 'duration_ns' in stage_data:
                    if stage_name not in all_stages:
                        all_stages[stage_name] = []
                    all_stages[stage_name].append(stage_data['duration_ns'] / 1e6)

    report_lines.append(f"\nPERFORMANCE BY STAGE:")
    for stage_name, times in sorted(all_stages.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True):
        avg_time = sum(times) / len(times)
        report_lines.append(f"  {stage_name:25} {avg_time:8.2f}ms (min: {min(times):.2f}ms, max: {max(times):.2f}ms)")

    # Overall statistics
    successful_measurements = [m for m in measurements if m['success']]
    if successful_measurements:
        total_durations = [m.get('total_duration_ns', 0) / 1e6 for m in successful_measurements]

        report_lines.append(f"\nOVERALL PERFORMANCE:")
        report_lines.append(f"  Total Duration - Median: {sorted(total_durations)[len(total_durations)//2]:.2f}ms, "
                          f"P95: {sorted(total_durations)[int(len(total_durations) * 0.95)]:.2f}ms")
        report_lines.append(f"  Range: {min(total_durations):.2f}ms - {max(total_durations):.2f}ms")

        # Bottleneck analysis
        if all_stages:
            bottleneck_stage = max(all_stages.items(), key=lambda x: sum(x[1]) / len(x[1]))
            bottleneck_avg = sum(bottleneck_stage[1]) / len(bottleneck_stage[1])
            overall_avg = sum(total_durations) / len(total_durations)

            report_lines.append(f"\nBOTTLENECK ANALYSIS:")
            report_lines.append(f"  Stage: {bottleneck_stage[0]}")
            report_lines.append(f"  Average Time: {bottleneck_avg:.2f}ms")
            report_lines.append(f"  Percentage of Total: {(bottleneck_avg / overall_avg * 100):.1f}%")

    report_lines.append(f"\n" + "=" * 80)

    return "\n".join(report_lines)

if __name__ == "__main__":
    print("DUDE Latency Measurement System loaded")
    print("Use DUDELatencyInstrumentation to wrap DUDE components")