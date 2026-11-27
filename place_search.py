import os
import requests


class PlaceSearchError(Exception):
    pass


def find_candidates(query):
    key = os.environ.get("KAKAO_REST_API_KEY")
    if not key:
        raise PlaceSearchError("서버에 KAKAO_REST_API_KEY를 설정한 뒤 다시 검색해 주세요.")

    terms = [query]
    brand, _, branch = query.rpartition(" ")
    if brand and branch.endswith("점") and len(branch) > 1:
        # 예: '빽다방 보정점' → '보정 빽다방'. 원래 검색 결과도 함께 보존한다.
        area = branch[:-1]
        terms.append(f"{area} {brand}")
        if not area.endswith(("동", "리", "읍", "면", "역")):
            terms.append(f"{area}동 {brand}")

    found = {}
    for index, term in enumerate(terms):
        if index == 2 and found:
            break
        try:
            res = requests.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                params={"query": term, "size": 5},
                headers={"Authorization": f"KakaoAK {key}"}, timeout=6,
            )
            res.raise_for_status()
            docs = res.json()["documents"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            if found:
                break
            raise PlaceSearchError("카카오 장소 검색에 실패했습니다. 서버의 REST API 키와 카카오맵 사용 설정, 네트워크를 확인해 주세요.") from exc
        for doc in docs:
            kid = doc["id"]
            address = doc["road_address_name"] or doc["address_name"]
            if kid.isascii() and kid.isdigit() and address:
                found.setdefault(kid, {"id": kid, "name": doc["place_name"],
                                       "address": address, "lot": doc["address_name"]})
    return list(found.values())
