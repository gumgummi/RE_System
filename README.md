# RE_System
## 가상환경 구축 (Windows 기준)
* **`python -m venv venv`**: `venv`라는 이름의 가상환경 생성 (뒤의 `venv`가 가상환경의 이름, 원하는 대로 지정)
  
* **`venv\Scripts\activate`**: 가상환경 활성화. 터미널 앞에 `(venv)`가 붙으면 성공
  
* **Powershell에서 오류 발생 시**: 관리자 권한으로 cmd 실행 후, `Set-ExecutionPolicy Unrestricted -Scope CurrentUser` 실행
  
* **`pip install --upgrade pip`**
  
* **`pip install -r requirements.txt`**: 라이브러리 설치 (파이썬 버전: 3.11.9)

## 레포지토리 설명
* **`review_data/`**: 추출한 리뷰를 모은 csv 파일 (가게이름, 플랫폼, 리뷰, 별점, 날짜 포함)
  
* **`crawling.py`**: 크롤링 모듈

* **`app.py`**: Flask 백엔드, db 생성, 검색, 필요시 크롤링 모듈 호출 담당

* **`templates/`**: 프론드엔드 html 파일

## 사용 방법
* `python app.py`: 서버 실행

* 콘솔 창에 뜬 주소 (http://127.0.0.1:5000) URL에 입력
