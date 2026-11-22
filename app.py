import os
import glob
import sqlite3
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from concurrent.futures import ThreadPoolExecutor

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
    conn.commit()
    conn.close()
    seed_from_csv_if_empty()


def crawl_store_live(store_name, target_count=50):
    """네이버와 카카오 크롤러를 병렬로 실행해 리뷰를 수집"""
    naver = NaverMapCrawler(headless=True, target_count=target_count)
    kakao = KakaoMapCrawler(headless=True, target_count=target_count)

    def _run_naver():
        try:
            return naver.crawl(store_name)
        except Exception as e:
            print(f"[NAVER CRAWL ERROR] '{store_name}': {e}")
            return []
        finally:
            naver.close()

    def _run_kakao():
        try:
            return kakao.crawl(store_name)
        except Exception as e:
            print(f"[KAKAO CRAWL ERROR] '{store_name}': {e}")
            return []
        finally:
            kakao.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_naver = executor.submit(_run_naver)
            future_kakao = executor.submit(_run_kakao)
            naver_data = future_naver.result()
            kakao_data = future_kakao.result()
        return naver_data + kakao_data
    except Exception as e:
        print(f"[CRAWL ERROR] '{store_name}' 병렬 크롤링 중 오류: {e}")
        return []

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

    print("=== DEBUG CRAWLED_REVIEWS ===")
    print("type:", type(crawled_reviews), "len:", len(crawled_reviews) if crawled_reviews is not None else None)
    if crawled_reviews:
        first = crawled_reviews[0]
        print("first type:", type(first))
        try:
            print("first dict:", dict(first))
        except Exception:
            print("first dir/attrs:", getattr(first, "__dict__", first))

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
        store_name = place["store_name"]
        conn.close()
        return redirect(url_for("search", q=store_name))

    conn.close()
    return redirect(url_for("index"))

@app.route("/refresh/<int:place_id>", methods=["POST"])
def refresh_reviews(place_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, store_name FROM places WHERE id = ?", (place_id,))
    place = cur.fetchone()
    conn.close()

    if not place:
        flash("해당 매장을 찾을 수 없습니다.", "warning")
        return redirect(url_for("index"))

    store_name = place["store_name"]
    crawled_rows = crawl_store_live(store_name, target_count=10)

    conn = get_db()
    cur = conn.cursor()
    added_count = append_new_crawled_reviews(cur, place_id, crawled_rows)
    recalculate_place_stats(cur, place_id, store_name)
    conn.commit()
    conn.close()

    if added_count > 0:
        flash(f"새 리뷰 {added_count}건을 추가하고 최신 상태로 갱신했습니다.", "success")
    else:
        flash("새로 추가된 리뷰가 없습니다. 기존 리뷰와 업데이트 시각을 최신화했습니다.", "info")

    return redirect(url_for("search", q=store_name))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)