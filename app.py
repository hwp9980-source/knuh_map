import streamlit as st
import streamlit.components.v1 as components

# 웹 페이지 기본 설정
st.set_page_config(page_title="강원대병원 상급종합병원 시나리오", page_icon="🏥", layout="wide")


# 웹 브라우저 창 크기에 맞춰 지도가 반응형으로 늘어나도록 CSS 주입
st.markdown("""
<style>
    /* 1. Streamlit 기본 상하단 여백 축소 (타이틀 삭제로 인한 상단 여백 1.5rem으로 조정) */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1rem !important;
    }

    /* 2. Streamlit이 생성하는 iframe(지도 렌더링 영역)의 높이를 화면의 95%로 확장 */
    iframe {
        height: 95vh !important; 
        min-height: 800px;
    }
</style>
""", unsafe_allow_html=True)

# Folium HTML 파일 읽기
try:
    with open("KNUH_Scenario_Map_RealData.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    
    # 웹 화면에 HTML 렌더링
    components.html(html_data, height=1000) # 지도 영역을 더 많이 확보하기 위해 기본값을 1000으로 증가
    
except FileNotFoundError:
    st.error("지도 HTML 파일을 찾을 수 없습니다. 파일명을 확인해주세요.")