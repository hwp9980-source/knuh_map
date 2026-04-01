import streamlit as st
import streamlit.components.v1 as components

# 웹 페이지 기본 설정
st.set_page_config(page_title="강원대병원 상급종합병원 시나리오", page_icon="🏥", layout="wide")

st.title("🏥 강원대학교병원 상급종합병원 지정 커버리지 분석")
st.markdown("강원대학교병원이 상급종합병원으로 추가 지정될 경우, 영서 북부 및 접경 지역의 의료 골든타임 확보 효과를 나타낸 지도입니다.")

# Folium HTML 파일 읽기
try:
    with open("KNUH_Scenario_Map_RealData.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    
    # 웹 화면에 HTML 렌더링 (높이 700px)
    components.html(html_data, height=700)
    
except FileNotFoundError:
    st.error("지도 HTML 파일을 찾을 수 없습니다. 파일명을 확인해주세요.")
