# RE_System
## 가상환경 구축 (Windows 기준)
* **`python -m venv venv`**: `venv`라는 이름의 가상환경 생성 (뒤의 `venv`가 가상환경의 이름, 원하는 대로 지정)
  
* **`venv\Scripts\activate`**: 가상환경 활성화. 터미널 앞에 `(venv)`가 붙으면 성공
  
* **Powershell에서 오류 발생 시**: 관리자 권한으로 cmd 실행 후, `Set-ExecutionPolicy Unrestricted -Scope CurrentUser` 실행
  
* **`pip install --upgrade pip`**
  
* **`pip install -r requirements.txt`**: 라이브러리 설치 (파이썬 버전: 3.10)

## 레포지토리 설명
* **`review_data/`**: 추출한 리뷰를 모은 csv 파일 (가게이름, 플랫폼, 리뷰, 별점, 날짜 포함)
  
* **`crawling.py`**: `stores.txt` 파일에 적힌 가게의 리뷰를 크롤링
  
* **`stores.txt`**: 크롤링할 가게 목록

## 사용 방법
* `stores.txt`에 크롤링할 가게 목록 기입

* `python .\crawling.py`: 프로그램 실행

* 클래스 내부의 `target_count`를 통해 추출한 리뷰 개수 조정

* `main`의 `save_individually` 플래그 활성화하여 가게별로 csv 파일 생성 가능

* `headless=True` 설정을 통해 브라우저를 띄우지 않고 실행 가능