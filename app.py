import os
import glob
import sqlite3
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for

from crawling import NaverMapCrawler, KakaoMapCrawler

app = Flask(__name__)
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
    conn.commit()
    conn.close()
    seed_from_csv_if_empty()


def crawl_store_live(store_name):
    """DB에 없을 때만 실제 네이버/카카오 크롤링 수행"""
    naver = NaverMapCrawler(headless=True)
    kakao = KakaoMapCrawler(headless=True)
    try:
        naver_data = naver.crawl(store_name)
        kakao_data = kakao.crawl(store_name)
        return naver_data + kakao_data
    except Exception as e:
        print(f"[CRAWL ERROR] '{store_name}' 크롤링 중 오류: {e}")
        return []
    finally:
        naver.close()
        kakao.close()


@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT store_name, avg_rating, review_count, updated_at
        FROM places
        ORDER BY updated_at DESC, id DESC
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

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM places WHERE store_name = ?", (query,))
    place = cur.fetchone()

    source_mode = "DB 캐시 조회"
    if not place:
        source_mode = "실시간 크롤링 후 DB 저장"
        crawled_rows = crawl_store_live(query)
        save_place_and_reviews(query, crawled_rows)
        cur.execute("SELECT * FROM places WHERE store_name = ?", (query,))
        place = cur.fetchone()

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
        conn.commit()
        store_name = place["store_name"]
        conn.close()
        return redirect(url_for("search", q=store_name))

    conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)