import os
import re
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup


class NaverMapCrawler:
    def __init__(self, headless=False):
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless')
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.wait = WebDriverWait(self.driver, 10)
        self.target_count = 50

    def _format_date(self, raw_date):
        match = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', raw_date)
        if match:
            year, month, day = match.groups()
            return f"{year}.{int(month):02d}.{int(day):02d}."
        
        short_match = re.search(r'(\d{1,2})\.(\d{1,2})\.', raw_date)
        if short_match:
            month, day = short_match.groups()
            return f"2026.{int(month):02d}.{int(day):02d}."
        return raw_date

    def crawl(self, store_name):
        print(f"\n=== [Naver] 크롤링 시작: {store_name} ===")
        reviews = []

        try:
            self.driver.get("https://map.naver.com")
            
            # 검색
            search_box = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.input_search")))
            search_box.clear()
            search_box.send_keys(store_name)
            search_box.send_keys(Keys.ENTER)
            time.sleep(2)

            # 리스트 vs 상세 스마트 분기
            try:
                WebDriverWait(self.driver, 3).until(
                    EC.frame_to_be_available_and_switch_to_it((By.ID, "searchIframe"))
                )
                first_place = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.UEzoS a.YTJkH, div.place_block")))
                first_place.click()
                self.driver.switch_to.default_content()
            except Exception:
                self.driver.switch_to.default_content()

            self.wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "entryIframe")))
            time.sleep(2)

            # 리뷰 탭 클릭
            try:
                review_tab = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., '리뷰')]")))
                review_tab.click()
                time.sleep(2)
            except:
                pass

            # 최신순 정렬 버튼 클릭
            try:
                recent_sort_btn = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "a.place_button_option[data-nlog-area*='sortrecent']"))
                )
                recent_sort_btn.click()
                time.sleep(2)
            except Exception as e:
                print(f"[Naver] 최신순 정렬 클릭 예외 (기본 정렬 유지): {e}")

            # 스크롤 및 더보기 루프
            click_count = 0
            while click_count < 25:
                current_reviews = self.driver.find_elements(By.CSS_SELECTOR, "li.place_apply_pui")
                if len(current_reviews) >= 60:
                    break
                self.driver.find_element(By.CSS_SELECTOR, "body").send_keys(Keys.END)
                time.sleep(1)
                try:
                    more_btn = self.driver.find_element(By.XPATH, "//a[contains(@class, 'fvwqf') or .//span[contains(text(), '펼쳐서 더보기')]]")
                    self.driver.execute_script("arguments[0].click();", more_btn)
                    click_count += 1
                    time.sleep(1.5)
                except:
                    break

            # 파싱
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            review_items = soup.select("li.place_apply_pui")
            collected = 0
            
            for item in review_items:
                if collected >= self.target_count:
                    break
                
                text_elem = item.select_one("div.pui__vn15t2")
                text = text_elem.get_text(separator=" ", strip=True) if text_elem else ""
                if len(text) <= 1:
                    continue
                
                rating_elem = item.select_one("div.pui__6abRMf")
                rating = ""
                if rating_elem:
                    match = re.search(r'\d+(\.\d+)?', rating_elem.get_text(strip=True))
                    if match:
                        rating = match.group()
                
                raw_date = ""
                for span in item.select("span.pui__gfuUIT span.pui__blind"):
                    span_text = span.get_text(strip=True)
                    if "년" in span_text:
                        raw_date = span_text
                        break
                if not raw_date:
                    time_elem = item.select_one("span.pui__gfuUIT time")
                    if time_elem:
                        raw_date = time_elem.get_text(strip=True)
                        
                reviews.append({
                    "Store": store_name,
                    "Platform": "Naver",
                    "Review": text,
                    "Rating": rating,
                    "Date": self._format_date(raw_date)
                })
                collected += 1
            
            print(f"[Naver] '{store_name}' 수집 완료: {len(reviews)}개")

        except Exception as e:
            print(f"[Naver] '{store_name}' 크롤링 중 오류 발생: {e}")

        return reviews

    def close(self):
        self.driver.quit()


class KakaoMapCrawler:
    def __init__(self, headless=False):
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless')
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.wait = WebDriverWait(self.driver, 10)
        self.target_count = 50

    def crawl(self, store_name):
        print(f"\n=== [Kakao] 크롤링 시작: {store_name} ===")
        reviews = []

        try:
            self.driver.get("https://map.kakao.com")
            time.sleep(2)

            # 검색
            kakao_search_box = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[id="search.keyword.query"]')))
            kakao_search_box.clear()
            kakao_search_box.send_keys(store_name)
            kakao_search_box.send_keys(Keys.ENTER)
            time.sleep(2)

            # 후기 미제공 체크 및 상세 진입
            first_item = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul#info\\.search\\.place\\.list > li.PlaceItem:first-child")))
            
            if "후기 미제공" in first_item.text:
                print(f"[Kakao] 경고: '{store_name}'은(는) '후기 미제공' 매장입니다.")
                return reviews

            moreview_link = first_item.find_element(By.CSS_SELECTOR, "a.moreview")
            detail_url = moreview_link.get_attribute("href")
            
            self.driver.get(detail_url)
            time.sleep(3)

            # 후기 탭 클릭
            try:
                review_tab_k = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '후기') or contains(text(), '방문리뷰')]")))
                review_tab_k.click()
                time.sleep(2)
            except:
                pass

            # 최신순 정렬
            try:
                sort_btn = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn_sort")))
                sort_btn.click()
                time.sleep(1)

                latest_option = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//ul[@class='list_sort']//a[contains(text(), '최신 순')]")))
                latest_option.click()
                time.sleep(2)
            except Exception as e:
                print(f"[Kakao] 최신 순 정렬 설정 예외: {e}")

            # 무한 스크롤
            scroll_attempts = 0
            while scroll_attempts < 40:
                soup_temp = BeautifulSoup(self.driver.page_source, 'html.parser')
                if len(soup_temp.select("ul.list_review p.desc_review")) >= self.target_count:
                    break
                self.driver.find_element(By.CSS_SELECTOR, "body").send_keys(Keys.PAGE_DOWN)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                scroll_attempts += 1

            # '더보기' 버튼 펼치기
            more_buttons = self.driver.find_elements(By.CSS_SELECTOR, "ul.list_review span.btn_more, ul.list_review a.btn_more")
            for btn in more_buttons:
                try:
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.1)
                except:
                    pass
            time.sleep(1)

            # 파싱
            soup_k = BeautifulSoup(self.driver.page_source, 'html.parser')
            review_items = soup_k.select("ul.list_review > li")
            
            collected = 0
            for item in review_items:
                if collected >= self.target_count:
                    break
                
                text_elem = item.select_one("p.desc_review")
                text = text_elem.get_text(separator=" ", strip=True) if text_elem else ""
                if not text:
                    continue
                
                rating = ""
                rating_elem = item.select_one("span.starred_grade")
                if rating_elem:
                    screen_outs = rating_elem.select("span.screen_out")
                    if len(screen_outs) >= 2:
                        match = re.search(r'\d+(\.\d+)?', screen_outs[1].get_text(strip=True))
                        if match:
                            rating = match.group()
                
                date_elem = item.select_one("span.txt_date")
                date = date_elem.get_text(strip=True) if date_elem else ""
                
                reviews.append({
                    "Store": store_name,
                    "Platform": "Kakao",
                    "Review": text,
                    "Rating": rating,
                    "Date": date
                })
                collected += 1
            
            print(f"[Kakao] '{store_name}' 수집 완료: {len(reviews)}개")

        except Exception as e:
            print(f"[Kakao] '{store_name}' 크롤링 중 오류 발생: {e}")

        return reviews

    def close(self):
        self.driver.quit()


def load_store_list(txt_path):
    """txt 파일에서 줄바꿈 기준으로 식당 이름 목록을 읽어오는 함수"""
    if not os.path.exists(txt_path):
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {txt_path}")
        
    with open(txt_path, "r", encoding="utf-8") as f:
        # 공백 제거 및 빈 줄 제외
        stores = [line.strip() for line in f if line.strip()]
    return stores


# =========================================================
# 실행부 (Main)
# =========================================================
if __name__ == "__main__":
    input_txt_path = "./stores.txt"     # 식당 목록이 담긴 txt 파일 경로
    save_dir = "./review_data"          # 결과 CSV를 저장할 폴더 경로
    save_individually = False           # True: 식당별 개별 CSV 저장 / False: 전체 식당 1개 CSV로 통합 저장

    os.makedirs(save_dir, exist_ok=True)
    store_list = load_store_list(input_txt_path)
    print(f"총 {len(store_list)}개의 식당 목록을 불러왔습니다: {store_list}")

    naver_crawler = NaverMapCrawler(headless=False)
    kakao_crawler = KakaoMapCrawler(headless=False)
    
    all_stores_reviews = []

    try:
        for store_name in store_list:
            naver_reviews = naver_crawler.crawl(store_name)
            kakao_reviews = kakao_crawler.crawl(store_name)
            combined = naver_reviews + kakao_reviews
            
            if save_individually and combined:
                df_single = pd.DataFrame(combined)
                safe_name = re.sub(r'[\\/*?:"<>|]', '_', store_name).replace(' ', '_')
                file_path = os.path.join(save_dir, f"reviews_{safe_name}.csv")
                df_single.to_csv(file_path, index=False, encoding="utf-8-sig")
                print(f"-> 개별 파일 저장 완료: '{file_path}'")
            else:
                all_stores_reviews.extend(combined)
                
            time.sleep(2)  # 다음 매장으로 넘어가기 전 과부하 방지 대기

        # 전체 통합 저장 모드일 경우
        if not save_individually and all_stores_reviews:
            df_all = pd.DataFrame(all_stores_reviews)
            file_path = os.path.join(save_dir, "all_stores_reviews.csv")
            df_all.to_csv(file_path, index=False, encoding="utf-8-sig")
            print(f"\n==========================================")
            print(f"전체 식당 통합 CSV 저장 완료: '{file_path}'")
            print(f"총 수집 리뷰 수: {len(df_all)}개")
            print(f"==========================================")

    finally:
        # 모든 작업이 끝나면 브라우저 종료
        naver_crawler.close()
        kakao_crawler.close()