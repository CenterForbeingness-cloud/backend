"""
admin_courses.py — Admin course CMS (catalog, schedule, weeks, product).
"""

from __future__ import annotations

from typing import Optional

from app.config import STRIPE_PRICE_BY_COURSE_SLUG, SUPABASE_DB_URL, logger
from app.daily_schedule import ScheduleDay, replace_schedule, validate_course_slug
from app.entitlements import BUNDLE_INCLUDED_COURSES
from app.models import (
    AdminCourseDetailResponse,
    AdminCourseItem,
    AdminCourseLesson,
    AdminCourseProduct,
    AdminCourseWeek,
    AdminCreateCourseRequest,
    AdminReplaceScheduleRequest,
    AdminReplaceWeeksRequest,
    AdminScheduleDayFull,
    AdminUpdateCourseRequest,
    AdminUpsertProductRequest,
)


def _require_db() -> None:
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")


def list_all_admin_courses() -> list[AdminCourseItem]:
    """All courses in DB (published and draft) plus env-only slugs for admin visibility."""
    by_slug: dict[str, AdminCourseItem] = {}

    if SUPABASE_DB_URL:
        try:
            from app.db import db_connection

            with db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        c.course_slug,
                        c.title,
                        c.is_published,
                        p.provider_price_id,
                        COUNT(DISTINCT w.id) AS week_count,
                        COUNT(DISTINCT s.id) AS day_count
                    FROM public.courses c
                    LEFT JOIN public.course_products p
                        ON p.course_slug = c.course_slug AND p.is_active = true
                    LEFT JOIN public.course_weeks w ON w.course_slug = c.course_slug
                    LEFT JOIN public.course_daily_schedule s ON s.course_slug = c.course_slug
                    GROUP BY c.course_slug, c.title, c.is_published, p.provider_price_id
                    ORDER BY c.title, c.course_slug
                    """
                )
                for row in cur.fetchall():
                    slug = str(row[0])
                    price_id = row[3] or STRIPE_PRICE_BY_COURSE_SLUG.get(slug)
                    by_slug[slug] = AdminCourseItem(
                        course_slug=slug,
                        title=str(row[1] or slug),
                        price_id=price_id,
                        is_published=bool(row[2]),
                        bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES.get(slug, ())),
                        week_count=int(row[4] or 0),
                        day_count=int(row[5] or 0),
                    )
        except Exception as exc:
            logger.exception("list_all_admin_courses failed: %s", exc)

    for slug in BUNDLE_INCLUDED_COURSES:
        if slug not in by_slug:
            by_slug[slug] = AdminCourseItem(
                course_slug=slug,
                title=slug.replace("-", " ").title(),
                price_id=STRIPE_PRICE_BY_COURSE_SLUG.get(slug),
                is_published=False,
                bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES[slug]),
            )

    for slug, price_id in STRIPE_PRICE_BY_COURSE_SLUG.items():
        if slug not in by_slug:
            by_slug[slug] = AdminCourseItem(
                course_slug=slug,
                title=slug.replace("-", " ").title(),
                price_id=price_id,
                is_published=False,
                bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES.get(slug, ())),
            )

    return sorted(by_slug.values(), key=lambda c: c.course_slug)


def get_admin_course_detail(course_slug: str) -> Optional[AdminCourseDetailResponse]:
    _require_db()
    validate_course_slug(course_slug)

    from app.db import db_connection

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT course_slug, title, description, is_published
            FROM public.courses
            WHERE course_slug = %s
            """,
            (course_slug,),
        )
        row = cur.fetchone()
        if row is None:
            return None

        title = str(row[1] or course_slug)
        description = row[2]
        is_published = bool(row[3])

        cur.execute(
            """
            SELECT
                w.week_number,
                w.title,
                w.description,
                l.lesson_number,
                l.title,
                l.content_ref
            FROM public.course_weeks w
            LEFT JOIN public.course_lessons l ON l.week_id = w.id
            WHERE w.course_slug = %s
            ORDER BY w.week_number, l.lesson_number
            """,
            (course_slug,),
        )
        weeks_by_num: dict[int, AdminCourseWeek] = {}
        for wn, wt, wd, ln, lt, cref in cur.fetchall():
            week = weeks_by_num.get(int(wn))
            if week is None:
                week = AdminCourseWeek(
                    week_number=int(wn),
                    title=str(wt or f"Week {wn}"),
                    description=wd,
                    lessons=[],
                )
                weeks_by_num[int(wn)] = week
            if ln is not None:
                week.lessons.append(
                    AdminCourseLesson(
                        lesson_number=int(ln),
                        title=str(lt or ""),
                        content_ref=cref,
                    )
                )

        cur.execute(
            """
            SELECT day_number, day_title, content
            FROM public.course_daily_schedule
            WHERE course_slug = %s
            ORDER BY day_number
            """,
            (course_slug,),
        )
        schedule_days = [
            AdminScheduleDayFull(
                day_number=int(r[0]),
                day_title=r[1],
                content=str(r[2] or ""),
            )
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT provider_product_id, provider_price_id, unit_amount_cents, currency, is_active
            FROM public.course_products
            WHERE course_slug = %s
            ORDER BY is_active DESC, id DESC
            LIMIT 1
            """,
            (course_slug,),
        )
        prod_row = cur.fetchone()
        env_price = STRIPE_PRICE_BY_COURSE_SLUG.get(course_slug)
        product: Optional[AdminCourseProduct] = None
        if prod_row:
            product = AdminCourseProduct(
                provider_product_id=str(prod_row[0]),
                provider_price_id=str(prod_row[1]),
                unit_amount_cents=int(prod_row[2]),
                currency=str(prod_row[3] or "usd"),
                is_active=bool(prod_row[4]),
                price_source="db",
            )
        elif env_price:
            product = AdminCourseProduct(
                provider_price_id=env_price,
                price_source="env",
            )

    weeks = [weeks_by_num[n] for n in sorted(weeks_by_num)]
    return AdminCourseDetailResponse(
        course_slug=course_slug,
        title=title,
        description=description,
        is_published=is_published,
        week_count=len(weeks),
        day_count=len(schedule_days),
        weeks=weeks,
        schedule_days=schedule_days,
        product=product,
        bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES.get(course_slug, ())),
        env_price_id=env_price,
    )


def create_admin_course(body: AdminCreateCourseRequest) -> AdminCourseDetailResponse:
    _require_db()
    validate_course_slug(body.course_slug)

    from app.db import db_connection

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.courses (course_slug, title, description, is_published)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    body.course_slug,
                    body.title.strip(),
                    (body.description or "").strip() or None,
                    body.is_published,
                ),
            )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError(f"Course already exists: {body.course_slug}") from exc
        raise

    detail = get_admin_course_detail(body.course_slug)
    if detail is None:
        raise RuntimeError("Course created but could not be loaded")
    return detail


def update_admin_course(
    course_slug: str,
    body: AdminUpdateCourseRequest,
) -> AdminCourseDetailResponse:
    _require_db()
    validate_course_slug(course_slug)

    if body.title is None and body.description is None and body.is_published is None:
        raise ValueError("Provide at least one field to update")

    from app.db import db_connection

    sets: list[str] = []
    params: list[object] = []
    if body.title is not None:
        sets.append("title = %s")
        params.append(body.title.strip())
    if body.description is not None:
        sets.append("description = %s")
        params.append(body.description.strip() or None)
    if body.is_published is not None:
        sets.append("is_published = %s")
        params.append(body.is_published)

    params.append(course_slug)
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE public.courses SET {', '.join(sets)} WHERE course_slug = %s",
            params,
        )
        if cur.rowcount == 0:
            raise ValueError(f"Course not found: {course_slug}")

    detail = get_admin_course_detail(course_slug)
    if detail is None:
        raise ValueError(f"Course not found: {course_slug}")
    return detail


def replace_admin_course_schedule(
    course_slug: str,
    body: AdminReplaceScheduleRequest,
) -> int:
    validate_course_slug(course_slug)
    if not body.days:
        raise ValueError("At least one schedule day is required")

    days = [
        ScheduleDay(
            day_number=d.day_number,
            day_title=d.day_title,
            content=d.content.strip(),
        )
        for d in body.days
    ]
    return replace_schedule(course_slug, days)


def replace_admin_course_weeks(
    course_slug: str,
    body: AdminReplaceWeeksRequest,
) -> AdminCourseDetailResponse:
    _require_db()
    validate_course_slug(course_slug)

    from app.db import db_connection

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public.courses WHERE course_slug = %s",
            (course_slug,),
        )
        if cur.fetchone() is None:
            raise ValueError(f"Course not found: {course_slug}")

        cur.execute(
            "DELETE FROM public.course_weeks WHERE course_slug = %s",
            (course_slug,),
        )

        for week in body.weeks:
            cur.execute(
                """
                INSERT INTO public.course_weeks (course_slug, week_number, title, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    course_slug,
                    week.week_number,
                    week.title.strip(),
                    (week.description or "").strip() or None,
                ),
            )
            week_id = cur.fetchone()[0]
            for lesson in week.lessons:
                cur.execute(
                    """
                    INSERT INTO public.course_lessons (week_id, lesson_number, title, content_ref)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        week_id,
                        lesson.lesson_number,
                        lesson.title.strip(),
                        (lesson.content_ref or "").strip() or None,
                    ),
                )

    detail = get_admin_course_detail(course_slug)
    if detail is None:
        raise ValueError(f"Course not found: {course_slug}")
    return detail


def upsert_admin_course_product(
    course_slug: str,
    body: AdminUpsertProductRequest,
) -> AdminCourseDetailResponse:
    _require_db()
    validate_course_slug(course_slug)

    from app.db import db_connection

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public.courses WHERE course_slug = %s",
            (course_slug,),
        )
        if cur.fetchone() is None:
            raise ValueError(f"Course not found: {course_slug}")

        cur.execute(
            """
            INSERT INTO public.course_products (
                course_slug, provider, provider_product_id, provider_price_id,
                currency, unit_amount_cents, is_active
            )
            VALUES (%s, 'stripe', %s, %s, %s, %s, %s)
            ON CONFLICT (course_slug) DO UPDATE SET
                provider_product_id = EXCLUDED.provider_product_id,
                provider_price_id = EXCLUDED.provider_price_id,
                currency = EXCLUDED.currency,
                unit_amount_cents = EXCLUDED.unit_amount_cents,
                is_active = EXCLUDED.is_active,
                updated_at = timezone('utc', now())
            """,
            (
                course_slug,
                body.provider_product_id.strip(),
                body.provider_price_id.strip(),
                body.currency.strip().lower() or "usd",
                body.unit_amount_cents,
                body.is_active,
            ),
        )

    detail = get_admin_course_detail(course_slug)
    if detail is None:
        raise ValueError(f"Course not found: {course_slug}")
    return detail
