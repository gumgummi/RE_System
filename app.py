import os
import glob
import sqlite3
from datetime import datetime
from contextlib import closing
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, abort, session
from concurrent.futures import ThreadPoolExecutor

from place_search import find_candidates, PlaceSearchError
from crawling import NaverMapCrawler, KakaoMapCrawler

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-in-prod")
DB_PATH = "reviews.db"
CSV_PATH = "all_stores_reviews.csv"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def dummy_ai_analysis(reviews):
    """AI 모델 연동 전까지 쓰는 임시 종합 분석 함수"""
    if not reviews:
        return {
            "summary": "분석할 리뷰 데이터가 아직 없습니다.",
            "total_count": 0,
            "avg_rating": "-",
        }

    ratings = []
    for r in reviews:
        val = r["Rating"] if isinstance(r, dict) else r["rating"]
        try:
            if val is not None and str(val).strip() != "":
                ratings.append(float(val))
        except ValueError:
            pass

    avg_rating = f"{sum(ratings) / len(ratings):.2f}" if ratings else "-"
    summary = (
        f"총 {len(reviews)}개의 리뷰가 수집되었습니다 (평균 평점: {avg_rating}). "
        "추후 학습된 AI 종합 요약/감성 분석 모델 결과가 이 영역에 표시됩니다."
    )
    return {
        "summary": summary,
        "total_count": len(reviews),
        "avg_rating": avg_rating,
    }


def save_place_and_reviews(store_name, review_rows):
    """매장 및 크롤링 리뷰를 DB에 저장하고 AI 종합 분석 결과 생성"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    analysis = dummy_ai_analysis(review_rows)

    cur.execute(
        """
        INSERT INTO places (store_name, summary, avg_rating, review_count, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(store_name) DO UPDATE SET
            summary = excluded.summary,
            avg_rating = excluded.avg_rating,
            review_count = excluded.review_count,
            updated_at = excluded.updated_at
        """,
        (
            store_name,
            analysis["summary"],
            analysis["avg_rating"],
            analysis["total_count"],
            now,
        ),
    )

    cur.execute("SELECT id FROM places WHERE store_name = ?", (store_name,))
    place_id = cur.fetchone()["id"]

    # 기존 크롤링 리뷰 중복 방지를 위해 삭제 후 재삽입 (갱신 시 깔끔함)
    cur.execute("DELETE FROM crawled_reviews WHERE place_id = ?", (place_id,))

    for row in review_rows:
        rating_raw = row.get("Rating", "")
        rating_str = "" if pd.isna(rating_raw) else str(rating_raw).strip()
        cur.execute(
            """
            INSERT INTO crawled_reviews (place_id, platform, review, rating, review_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                place_id,
                str(row.get("Platform", "")).strip(),
                str(row.get("Review", "")).strip(),
                rating_str,
                str(row.get("Date", "")).strip(),
            ),
        )

    conn.commit()
    conn.close()


def seed_from_csv_if_empty():
  """DB가 비어 있을 때 review_data/all_stores_reviews_*.csv 파일들을 모두 적재"""
  conn = get_db()
  cur = conn.cursor()
  cur.execute("SELECT COUNT(*) AS cnt FROM places")
  count = cur.fetchone()["cnt"]
  conn.close()

  if count > 0:
    return

  csv_files = sorted(glob.glob(os.path.join("review_data", "all_stores_reviews*.csv")))
  if not csv_files:
    return

  for csv_path in csv_files:
    df = pd.read_csv(csv_path)
    if df.empty or "Store" not in df.columns:
      continue
    print(f"[INIT] '{csv_path}' 적재 중 ({len(df)}행)...")
    for store_name, group in df.groupby("Store"):
      records = group.to_dict(orient="records")
      save_place_and_reviews(str(store_name).strip(), records)

  print("[INIT] 전체 CSV DB 적재 완료.")


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT UNIQUE NOT NULL,
            summary TEXT,
            avg_rating TEXT,
            review_count INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crawled_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL,
            platform TEXT,
            review TEXT,
            rating TEXT,
            review_date TEXT,
            FOREIGN KEY (place_id) REFERENCES places (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL,
            nickname TEXT NOT NULL,
            rating REAL NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (place_id) REFERENCES places (id)
        )
        """
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS map_places (
            kakao_id TEXT PRIMARY KEY,
            place_id INTEGER NOT NULL UNIQUE REFERENCES places(id),
            name TEXT NOT NULL, address TEXT NOT NULL, lot TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    seed_from_csv_if_empty()


def crawl_store_live(candidate, target_count=50):
    def read_platform(crawler_type, **identity):
        crawler = None
        try:
            crawler = crawler_type(headless=True, target_count=target_count)
            return crawler.crawl(candidate["name"], **identity)
        except Exception as exc:
            app.logger.warning("%s 수집 실패: %s", crawler_type.__name__, exc)
            return []
        finally:
            if crawler:
                crawler.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        naver = executor.submit(read_platform, NaverMapCrawler,
                                addresses=(candidate["address"], candidate["lot"]))
        kakao = executor.submit(read_platform, KakaoMapCrawler, kakao_id=candidate["id"])
        return naver.result() + kakao.result()


def append_new_crawled_reviews(cur, place_id, review_rows):
    """기존 리뷰와 중복되지 않는 새 크롤링 리뷰만 추가하고 추가된 건수를 반환"""
    cur.execute(
        "SELECT platform, review, review_date FROM crawled_reviews WHERE place_id = ?",
        (place_id,),
    )
    existing_keys = {
        (
            str(r["platform"] or "").strip(),
            str(r["review"] or "").strip(),
            str(r["review_date"] or "").strip(),
        )
        for r in cur.fetchall()
    }

    added_count = 0
    for row in review_rows:
        platform = str(row.get("Platform", "")).strip()
        review_text = str(row.get("Review", "")).strip()
        review_date = str(row.get("Date", "")).strip()
        rating_raw = row.get("Rating", "")
        rating_str = "" if pd.isna(rating_raw) else str(rating_raw).strip()

        if not review_text:
            continue

        key = (platform, review_text, review_date)
        if key in existing_keys:
            continue

        cur.execute(
            """
            INSERT INTO crawled_reviews (place_id, platform, review, rating, review_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (place_id, platform, review_text, rating_str, review_date),
        )
        existing_keys.add(key)
        added_count += 1

    return added_count

def recalculate_place_stats(cur, place_id, store_name):
    """crawled_reviews + user_reviews 기준으로 review_count, avg_rating, summary 갱신"""
    cur.execute(
        "SELECT platform, review, rating, review_date FROM crawled_reviews WHERE place_id = ?",
        (place_id,),
    )
    crawled_rows = cur.fetchall()

    cur.execute(
        "SELECT rating FROM user_reviews WHERE place_id = ?",
        (place_id,),
    )
    user_rows = cur.fetchall()

    ratings = []
    for r in crawled_rows:
        val = r["rating"]
        try:
            if val is not None and str(val).strip() != "":
                ratings.append(float(str(val).strip()))
        except ValueError:
            pass

    for r in user_rows:
        val = r["rating"]
        try:
            if val is not None:
                ratings.append(float(val))
        except (ValueError, TypeError):
            pass

    total_crawled = len(crawled_rows)
    avg_rating_str = f"{sum(ratings) / len(ratings):.2f}" if ratings else "-"
    summary_text = (
        f"총 {total_crawled}개의 크롤링 리뷰와 {len(user_rows)}개의 직접 리뷰가 반영되었습니다 "
        f"(평균 평점: {avg_rating_str}). "
        "추후 학습된 AI 종합 요약/감성 분석 모델 결과가 이 영역에 표시됩니다."
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        UPDATE places
        SET review_count = ?, avg_rating = ?, summary = ?, updated_at = ?
        WHERE id = ?
        """,
        (total_crawled, avg_rating_str, summary_text, now, place_id),
    )

@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, COALESCE(m.name, p.store_name) AS store_name, p.avg_rating,
               p.review_count, p.updated_at
        FROM places p LEFT JOIN map_places m ON m.place_id = p.id
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 15
        """
    )
    recent_places = cur.fetchall()
    conn.close()
    return render_template("index.html", recent_places=recent_places)


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))

    if len(query) > 200:
        return render_template("choose.html", query=query, candidates=[], error="검색어는 200자 이내로 입력해 주세요."), 400
    saved = session.get("chosen_places", {}).get(query)
    if saved and not request.args.get("choose"):
        with closing(get_db()) as conn:
            row = conn.execute("SELECT place_id FROM map_places WHERE kakao_id = ?", (saved,)).fetchone()
        if row:
            return redirect(url_for("result", place_id=row["place_id"], q=query))
    try:
        candidates = find_candidates(query)
    except PlaceSearchError as exc:
        return render_template("choose.html", query=query, candidates=[], error=str(exc)), 503
    return render_template("choose.html", query=query, candidates=candidates)


@app.post("/select-place")
def select_place():
    query = request.form.get("q", "").strip()
    kid = request.form.get("kakao_id", "")
    if not query or len(query) > 200 or not kid.isascii() or not kid.isdigit():
        abort(400)
    # 제출한 이름·주소를 신뢰하지 않고 지도 검색 결과에서 선택 ID를 다시 찾는다.
    try:
        candidates = find_candidates(query)
    except PlaceSearchError as exc:
        return render_template("choose.html", query=query, candidates=[], error=str(exc)), 503
    candidate = next((c for c in candidates if c["id"] == kid), None)
    if candidate is None:
        return render_template("choose.html", query=query, candidates=candidates,
                               error="검색 결과가 바뀌었습니다. 지점을 다시 선택해 주세요."), 409

    with closing(get_db()) as conn:
        match = conn.execute("SELECT place_id FROM map_places WHERE kakao_id = ?", (kid,)).fetchone()
    if match:
        place_id = match["place_id"]
    else:
        reviews = crawl_store_live(candidate)
        with closing(get_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            # 기존 검색어 캐시와 서로 다른 동명 매장의 리뷰가 섞이지 않게 한다.
            match = conn.execute("SELECT place_id FROM map_places WHERE kakao_id = ?", (kid,)).fetchone()
            if match:
                place_id = match["place_id"]
            else:
                # places.store_name은 기존 스키마의 UNIQUE 키다. 표시 이름은 map_places에 보관한다.
                place_id = conn.execute(
                    "INSERT INTO places (store_name) VALUES ('kakao:' || ? || ':' || hex(randomblob(16)))",
                    (kid,),
                ).lastrowid
                conn.execute("""INSERT INTO map_places (kakao_id, place_id, name, address, lot)
                                VALUES (?, ?, ?, ?, ?)""",
                             (kid, place_id, candidate["name"], candidate["address"], candidate["lot"]))
            append_new_crawled_reviews(conn.cursor(), place_id, reviews)
            recalculate_place_stats(conn.cursor(), place_id, candidate["name"])
        if not reviews:
            flash("선택한 매장의 리뷰를 가져오지 못했습니다. 최신 리뷰 확인으로 다시 시도해 주세요.", "warning")

    choices = dict(session.get("chosen_places", {}))
    choices[query] = kid
    session["chosen_places"] = dict(list(choices.items())[-5:])
    return redirect(url_for("result", place_id=place_id, q=query), code=303)


@app.get("/places/<int:place_id>")
def result(place_id):
    conn = get_db()
    cur = conn.cursor()
    place = cur.execute("""
        SELECT p.*, COALESCE(m.name, p.store_name) AS display_name, m.address, m.kakao_id
        FROM places p LEFT JOIN map_places m ON m.place_id = p.id WHERE p.id = ?
    """, (place_id,)).fetchone()
    if not place:
        conn.close()
        abort(404)
    query = request.args.get("q") or place["display_name"]
    source_mode = "DB 캐시 조회" if place["kakao_id"] else "지점 확인 전 기록"
    cur.execute(
        "SELECT * FROM crawled_reviews WHERE place_id = ? ORDER BY id ASC",
        (place["id"],),
    )
    crawled_reviews = cur.fetchall()

    cur.execute(
        "SELECT * FROM user_reviews WHERE place_id = ? ORDER BY id DESC",
        (place["id"],),
    )
    user_reviews = cur.fetchall()
    conn.close()

    return render_template(
        "result.html",
        query=query,
        place=place,
        source_mode=source_mode,
        crawled_reviews=crawled_reviews,
        user_reviews=user_reviews,
    )


@app.route("/review/<int:place_id>", methods=["POST"])
def add_review(place_id):
    nickname = request.form.get("nickname", "").strip() or "익명"
    rating_raw = request.form.get("rating", "5").strip()
    content = request.form.get("content", "").strip()

    try:
        rating = float(rating_raw)
    except ValueError:
        rating = 5.0

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT store_name FROM places WHERE id = ?", (place_id,))
    place = cur.fetchone()

    if place and content:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur.execute(
            """
            INSERT INTO user_reviews (place_id, nickname, rating, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (place_id, nickname, rating, content, now),
        )
        recalculate_place_stats(cur, place_id, place["store_name"])
        conn.commit()
        conn.close()
        return redirect(url_for("result", place_id=place_id))

    conn.close()
    return redirect(url_for("index"))

@app.route("/refresh/<int:place_id>", methods=["POST"])
def refresh_reviews(place_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""SELECT p.store_name, m.kakao_id AS id, m.name, m.address, m.lot
                   FROM places p LEFT JOIN map_places m ON m.place_id = p.id WHERE p.id = ?""", (place_id,))
    place = cur.fetchone()
    conn.close()

    if not place:
        flash("해당 매장을 찾을 수 없습니다.", "warning")
        return redirect(url_for("index"))

    if not place["id"]:
        return redirect(url_for("search", q=place["store_name"], choose=1))
    store_name = place["name"]
    crawled_rows = crawl_store_live(dict(place), target_count=10)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    added_count = append_new_crawled_reviews(cur, place_id, crawled_rows)
    recalculate_place_stats(cur, place_id, store_name)
    conn.commit()
    conn.close()

    if added_count > 0:
        flash(f"새 리뷰 {added_count}건을 추가하고 최신 상태로 갱신했습니다.", "success")
    else:
        flash("새로 추가된 리뷰가 없습니다. 기존 리뷰와 업데이트 시각을 최신화했습니다.", "info")

    return redirect(url_for("result", place_id=place_id))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)