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


class BaseMapCrawler:
    """공통 드라이버 설정 및 브라우저 관리 부모 클래스"""
    def __init__(self, headless=False):
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless')
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.wait = WebDriverWait(self.driver, 10)
        self.target_count = 50

    def close(self):
        if self.driver:
            self.driver.quit()


class NaverMapCrawler(BaseMapCrawler):
    def __init__(self, headless=False):
        super().__init__(headless)

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

    def _navigate_to_store(self, store_name):
        """책임 1: 검색 및 상세 페이지 진입 (다중 결과 리스트 클릭 및 단일 결과 직행 대응)"""
        self.driver.get("https://map.naver.com")
        
        search_box = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.input_search")))
        search_box.clear()
        search_box.send_keys(store_name)
        search_box.send_keys(Keys.ENTER)
        time.sleep(2)

        # 1단계: searchIframe 진입 시도 (검색 결과 목록이 존재하는지 확인)
        try:
            WebDriverWait(self.driver, 3).until(
                EC.frame_to_be_available_and_switch_to_it((By.ID, "searchIframe"))
            )
            
            # 리스트 컨테이너 안의 첫 번째 매장 클릭 시도
            try:
                first_place = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//div[@id='_pcmap_list_scroll_container']//ul/li[1]//a[not(contains(@class, 'place_thumb'))][1]")
                    )
                )
                first_place.click()
                print(f"[Naver] '{store_name}' 검색 결과 목록에서 첫 번째 항목을 선택했습니다.")
            except Exception:
                # 목록은 떴으나 리스트 구조가 다르거나 단일 결과 성격인 경우
                print(f"[Naver] '{store_name}' 검색 결과 목록 내 클릭 가능한 항목을 찾지 못했습니다.")
            
            self.driver.switch_to.default_content()

        except Exception:
            # 2단계: searchIframe이 없거나 뜨지 않았다면, 네이버가 검색 결과 1개로 판단해 
            # 곧바로 상세 페이지로 리다이렉트한 경우이므로 기본 컨텍스트 유지 후 바로 entryIframe 진입 시도
            print(f"[Naver] '{store_name}' 검색 결과가 1개여서 상세 페이지로 바로 이동했습니다.")
            self.driver.switch_to.default_content()

        # 공통: 최종적으로 상세 페이지 영역인 entryIframe으로 진입
        try:
            WebDriverWait(self.driver, 4).until(
                EC.frame_to_be_available_and_switch_to_it((By.ID, "entryIframe"))
            )
            time.sleep(2)
            return True
        except Exception as e:
            print(f"[Naver] '{store_name}' 상세 페이지(entryIframe) 진입 실패: {e}")
            self.driver.switch_to.default_content()
            return False

    def _setup_review_tab_and_sort(self):
        """책임 2: 리뷰 탭 클릭 및 최신순 정렬"""
        try:
            review_tab = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(., '리뷰')]"))
            )
            review_tab.click()
            time.sleep(2)
        except:
            return False

        try:
            recent_sort_btn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a.place_button_option[data-nlog-area*='sortrecent']"))
            )
            recent_sort_btn.click()
            time.sleep(2)
        except Exception as e:
            print(f"[Naver] 최신순 정렬 클릭 예외 (기본 정렬 유지): {e}")
        return True

    def _scroll_reviews(self):
        """책임 3: 무한 스크롤 및 '펼쳐서 더보기' 클릭 (조기 탈출 적용)"""
        click_count = 0
        last_review_count = 0
        same_count_iterations = 0

        while click_count < 25:
            current_reviews = self.driver.find_elements(By.CSS_SELECTOR, "li.place_apply_pui")
            current_count = len(current_reviews)
            
            if current_count >= 60 or current_count >= self.target_count:
                break
                
            if current_count == last_review_count:
                same_count_iterations += 1
                if same_count_iterations >= 2:  # 네이버는 변화가 2번 없으면 빠르게 탈출
                    print(f"[Naver] 모든 리뷰를 불러왔습니다 (총 {current_count}개).")
                    break
            else:
                same_count_iterations = 0
                
            last_review_count = current_count

            self.driver.find_element(By.CSS_SELECTOR, "body").send_keys(Keys.END)
            time.sleep(1)
            
            try:
                more_btn = self.driver.find_element(By.XPATH, "//a[contains(@class, 'fvwqf') or .//span[contains(text(), '펼쳐서 더보기')]]")
                self.driver.execute_script("arguments[0].click();", more_btn)
                click_count += 1
                time.sleep(1.5)
            except:
                # 더보기 버튼이 없으면 잠시 대기 후 반복문 탈출
                time.sleep(1)
                break

    def _parse_reviews(self, store_name):
        """책임 4: HTML 파싱 및 데이터 추출"""
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        review_items = soup.select("li.place_apply_pui")
        reviews = []
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
            
        return reviews

    def crawl(self, store_name):
        print(f"\n=== [Naver] 크롤링 시작: {store_name} ===")
        if not self._navigate_to_store(store_name):
            print(f"[Naver] '{store_name}' 검색 결과를 찾을 수 없거나 진입에 실패했습니다.")
            return []

        if not self._setup_review_tab_and_sort():
            print(f"[Naver] '{store_name}' 리뷰 탭을 찾을 수 없습니다.")
            return []

        self._scroll_reviews()
        reviews = self._parse_reviews(store_name)
        print(f"[Naver] '{store_name}' 수집 완료: {len(reviews)}개")
        return reviews


class KakaoMapCrawler(BaseMapCrawler):
    def __init__(self, headless=False):
        super().__init__(headless)

    def _navigate_to_store(self, store_name):
        """책임 1: 카카오맵 검색 및 상세 페이지 진입 (미제공/0건 사전 필터링)"""
        self.driver.get("https://map.kakao.com")
        time.sleep(2)

        kakao_search_box = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[id="search.keyword.query"]')))
        kakao_search_box.clear()
        kakao_search_box.send_keys(store_name)
        kakao_search_box.send_keys(Keys.ENTER)
        time.sleep(2)

        try:
            first_item = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul#info\\.search\\.place\\.list > li.PlaceItem:first-child"))
            )
        except Exception:
            return None

        # 1. '후기 미제공' 여부 확인 (HIDDEN 클래스가 없을 때만 실제 미제공 상태)
        blind_elems = first_item.find_elements(By.CSS_SELECTOR, 'span[data-id="scoreBlind"]')
        if blind_elems and "HIDDEN" not in blind_elems[0].get_attribute("class"):
            print(f"[Kakao] 스킵: '{store_name}'은(는) '후기 미제공' 매장입니다.")
            return False

        # 2. 후기 건수('0건') 확인
        score_count_elems = first_item.find_elements(By.CSS_SELECTOR, 'a[data-id="numberofscore"]')
        if score_count_elems:
            count_text = score_count_elems[0].text.strip()  # 예: "0건", "12건"
            if count_text == "0건":
                print(f"[Kakao] 스킵: '{store_name}'의 카카오 후기가 0건입니다.")
                return False

        moreview_link = first_item.find_element(By.CSS_SELECTOR, "a.moreview")
        detail_url = moreview_link.get_attribute("href")
        
        self.driver.get(detail_url)
        time.sleep(3)
        return True

    def _setup_review_tab_and_sort(self):
        """책임 2: 후기 탭 클릭 및 최신 순 정렬"""
        try:
            review_tab_k = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '후기') or contains(text(), '방문리뷰')]"))
            )
            review_tab_k.click()
            time.sleep(2)
        except:
            pass

        try:
            sort_btn = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn_sort")))
            sort_btn.click()
            time.sleep(1)

            latest_option = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//ul[@class='list_sort']//a[contains(text(), '최신 순')]")))
            latest_option.click()
            time.sleep(2)
        except Exception as e:
            print(f"[Kakao] 최신 순 정렬 설정 예외: {e}")

    def _scroll_and_expand_reviews(self):
        """책임 3: 무한 스크롤 및 긴 리뷰 '더보기' 펼치기 (조기 탈출 적용)"""
        scroll_attempts = 0
        last_review_count = 0
        same_count_iterations = 0

        while scroll_attempts < 40:
            soup_temp = BeautifulSoup(self.driver.page_source, 'html.parser')
            current_reviews = soup_temp.select("ul.list_review p.desc_review")
            current_count = len(current_reviews)
            
            # 목표 수량(50개)을 채웠으면 즉시 탈출
            if current_count >= self.target_count:
                break
                
            # 스크롤을 내렸는데도 리뷰 개수가 늘어나지 않는 경우 (끝에 도달함)
            if current_count == last_review_count:
                same_count_iterations += 1
                if same_count_iterations >= 3:  # 3번 시도 동안 변화가 없으면 종료
                    print(f"[Kakao] 모든 리뷰를 불러왔습니다 (총 {current_count}개).")
                    break
            else:
                same_count_iterations = 0  # 개수가 늘어났으면 카운트 초기화
                
            last_review_count = current_count

            # 페이지 아래로 스크롤
            self.driver.find_element(By.CSS_SELECTOR, "body").send_keys(Keys.PAGE_DOWN)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            scroll_attempts += 1

        # 긴 리뷰 '더보기' 버튼 펼치기
        more_buttons = self.driver.find_elements(By.CSS_SELECTOR, "ul.list_review span.btn_more, ul.list_review a.btn_more")
        for btn in more_buttons:
            try:
                self.driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.1)
            except:
                pass
        time.sleep(1)

    def _parse_reviews(self, store_name):
        """책임 4: HTML 파싱 및 데이터 추출"""
        soup_k = BeautifulSoup(self.driver.page_source, 'html.parser')
        review_items = soup_k.select("ul.list_review > li")
        reviews = []
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
            
        return reviews

    def crawl(self, store_name):
        print(f"\n=== [Kakao] 크롤링 시작: {store_name} ===")
        nav_result = self._navigate_to_store(store_name)
        if nav_result is None:
            print(f"[Kakao] '{store_name}' 검색 결과를 찾을 수 없습니다.")
            return []
        if nav_result is False:
            return []

        self._setup_review_tab_and_sort()
        self._scroll_and_expand_reviews()
        reviews = self._parse_reviews(store_name)
        print(f"[Kakao] '{store_name}' 수집 완료: {len(reviews)}개")
        return reviews


def load_store_list(txt_path):
    if not os.path.exists(txt_path):
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {txt_path}")
    with open(txt_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


if __name__ == "__main__":
    input_txt_path = "./stores.txt"
    save_dir = "./review_data"
    save_individually = False

    os.makedirs(save_dir, exist_ok=True)
    store_list = load_store_list(input_txt_path)
    print(f"총 {len(store_list)}개의 식당 목록을 불러왔습니다: {store_list}")

    naver_crawler = NaverMapCrawler(headless=True)
    kakao_crawler = KakaoMapCrawler(headless=True)
    
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
                
            time.sleep(2)

        if not save_individually and all_stores_reviews:
            df_all = pd.DataFrame(all_stores_reviews)
            file_path = os.path.join(save_dir, "all_stores_reviews.csv")
            df_all.to_csv(file_path, index=False, encoding="utf-8-sig")
            print(f"\n==========================================")
            print(f"전체 식당 통합 CSV 저장 완료: '{file_path}'")
            print(f"총 수집 리뷰 수: {len(df_all)}개")
            print(f"==========================================")

    finally:
        naver_crawler.close()
        kakao_crawler.close()