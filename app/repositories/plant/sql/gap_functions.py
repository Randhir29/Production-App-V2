"""PL/pgSQL helpers the V2 gap query depends on, verbatim from agent.md §5.7.4."""

from sqlalchemy import text

SQL_CREATE_GET_GAP_BETWEEN_TIMES = text(
    """
CREATE OR REPLACE FUNCTION getGapBetweenTimes(process_time TEXT, prev_process_time text, start_time text, end_time TEXT)
RETURNS INTERVAL AS $$
begin
  return case
    when prev_process_time is null then case
        when process_time::time <= start_time::time then '0 seconds'::interval
        when process_time::time >= end_time::time   then end_time::time - start_time::time
        else process_time::time - start_time::time end
    WHEN process_time::time > end_time::time AND prev_process_time::time > end_time::time THEN '0 seconds'::interval
    WHEN process_time::time < start_time::time AND prev_process_time::time < start_time::time THEN '0 seconds'::interval
    WHEN prev_process_time::time >= start_time::time AND process_time::time <= end_time::time THEN process_time::time - prev_process_time::time
    WHEN prev_process_time::time < start_time::time AND process_time::time > end_time::time THEN end_time::time - start_time::time
    WHEN prev_process_time::time < start_time::time AND process_time::time between start_time::time and end_time::time THEN process_time::time - start_time::time
    WHEN process_time::time > end_time::time AND prev_process_time::time between start_time::time and end_time::time THEN end_time::time - prev_process_time::time
    ELSE '2 days'::interval
  END;
END; $$ LANGUAGE plpgsql;
"""
)

SQL_CREATE_GET_END_GAP_FOR_PHASE = text(
    """
CREATE OR REPLACE FUNCTION getEndGapForPhase(last_cyl_time TIME, phase_start text, phase_end text)
RETURNS INTERVAL AS $$
begin
  return case
    WHEN last_cyl_time < phase_start::time THEN phase_end::time - phase_start::time
    WHEN last_cyl_time > phase_end::time THEN '0 seconds'::interval
    WHEN last_cyl_time between phase_start::time and phase_end::time THEN phase_end::time - last_cyl_time
    ELSE '0 seconds'::interval
  END;
END; $$ LANGUAGE plpgsql;
"""
)

GAP_FUNCTION_NAMES = ("getgapbetweentimes", "getendgapforphase")  # pg_proc stores lower-case
