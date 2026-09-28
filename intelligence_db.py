import os
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv("/app/.env")


def get_connection():
    return psycopg2.connect(
        host=os.getenv("ROBIN_DB_HOST", "slax-postgres"),
        port=int(os.getenv("ROBIN_DB_PORT", "5432")),
        dbname=os.getenv("ROBIN_DB_NAME", "slaxsecurity"),
        user=os.getenv("ROBIN_DB_USER", "slax"),
        password=os.getenv("ROBIN_DB_PASSWORD"),
    )


def normalize_onion(link):
    if not link:
        return None

    try:
        parsed = urlparse(link)
        host = (parsed.hostname or "").strip().lower()
    except Exception:
        return None

    if not host.endswith(".onion"):
        return None

    return host


def start_search_run(query):
    """
    Creates one logical Robin search execution.
    Returns the generated search_run_id.
    """
    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO robin.search_runs (
                        search_query
                    )
                    VALUES (%s)
                    RETURNING id
                    """,
                    (query,),
                )

                run_id = cur.fetchone()[0]

        return run_id

    except Exception as exc:
        print(f"[INTELLIGENCE DB] Failed starting search run: {exc}")
        return None

    finally:
        if conn:
            conn.close()


def finish_search_run(run_id, result_count):
    """
    Marks a search execution as completed.
    """
    if run_id is None:
        return False

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE robin.search_runs
                    SET
                        finished_at = NOW(),
                        result_count = %s,
                        status = 'completed'
                    WHERE id = %s
                    """,
                    (result_count, run_id),
                )

        return True

    except Exception as exc:
        print(f"[INTELLIGENCE DB] Failed finishing search run {run_id}: {exc}")
        return False

    finally:
        if conn:
            conn.close()



def fail_search_run(run_id):
    """
    Marks a search execution as failed.
    """
    if run_id is None:
        return False

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE robin.search_runs
                    SET
                        finished_at = NOW(),
                        status = 'failed'
                    WHERE id = %s
                    """,
                    (run_id,),
                )

        return True

    except Exception as exc:
        print(
            f"[INTELLIGENCE DB] "
            f"Failed marking search run {run_id} as failed: {exc}"
        )
        return False

    finally:
        if conn:
            conn.close()


def record_observation(link, title, query, engine, run_id=None):
    """
    Records an onion observation.

    Legacy calls without run_id remain supported.
    New calls associate the observation with a search run.
    """
    onion = normalize_onion(link)

    if not onion:
        return False

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                # Keep/update the service inventory.
                cur.execute(
                    """
                    INSERT INTO robin.onion_services (
                        onion_address,
                        title,
                        first_seen,
                        last_seen,
                        search_hits,
                        engine_count
                    )
                    VALUES (
                        %s,
                        %s,
                        NOW(),
                        NOW(),
                        1,
                        1
                    )
                    ON CONFLICT (onion_address)
                    DO UPDATE SET
                        title = CASE
                            WHEN EXCLUDED.title IS NOT NULL
                                 AND EXCLUDED.title <> ''
                            THEN EXCLUDED.title
                            ELSE robin.onion_services.title
                        END,
                        last_seen = NOW(),
                        search_hits = robin.onion_services.search_hits + 1
                    """,
                    (onion, title),
                )

                # Avoid duplicate observations from the same engine
                # inside the same logical search run.
                if run_id is not None:
                    cur.execute(
                        """
                        SELECT 1
                        FROM robin.onion_observations
                        WHERE search_run_id = %s
                          AND search_engine = %s
                          AND onion_address = %s
                        LIMIT 1
                        """,
                        (run_id, engine, onion),
                    )

                    if cur.fetchone():
                        return False

                cur.execute(
                    """
                    INSERT INTO robin.onion_observations (
                        onion_address,
                        search_query,
                        search_engine,
                        title,
                        search_run_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        onion,
                        query,
                        engine,
                        title,
                        run_id,
                    ),
                )

                # Recalculate number of distinct engines that have
                # observed this service across the complete history.
                cur.execute(
                    """
                    UPDATE robin.onion_services
                    SET engine_count = (
                        SELECT COUNT(DISTINCT search_engine)
                        FROM robin.onion_observations
                        WHERE onion_address = %s
                    )
                    WHERE onion_address = %s
                    """,
                    (onion, onion),
                )

        return True

    except Exception as exc:
        print(f"[INTELLIGENCE DB] Failed recording {onion}: {exc}")
        return False

    finally:
        if conn:
            conn.close()
