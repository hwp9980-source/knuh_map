import streamlit as st
import streamlit.components.v1 as components

# 웹 페이지 기본 설정
st.set_page_config(page_title="강원대병원 상급종합병원 시나리오", page_icon="🏥", layout="wide")

st.title("🏥 강원대학교병원 상급종합병원 지정 커버리지 분석")
st.markdown("강원대학교병원이 상급종합병원으로 추가 지정될 경우, 영서 북부 및 접경 지역의 의료 골든타임 확보 효과를 나타낸 지도입니다.")

# 웹 브라우저 창 크기에 맞춰 지도가 반응형으로 늘어나도록 CSS 주입
st.markdown("""
<style>
    /* 1. Streamlit 기본 상하단 여백 대폭 축소 (윗부분 잘림 방지를 위해 상단 여백 3.5rem 확보) */
    .block-container {
        padding-top: 3.5rem !important;
        padding-bottom: 1rem !important;
    }
    
    /* 2. 헤드라인(h1) 제목 크기 및 하단 여백 축소 */
    h1 {
        font-size: 1.8rem !important;
        padding-bottom: 0rem !important;
    }
    
    /* 3. 설명 줄(p) 여백 축소 */
    p {
        margin-bottom: 0.5rem !important;
    }

    /* 4. Streamlit이 생성하는 iframe(지도 렌더링 영역)의 높이를 화면의 85%로 설정 */
    iframe {
        height: 85vh !important; 
        min-height: 600px;
    }
</style>
""", unsafe_allow_html=True)

# Folium HTML 파일 읽기
try:
    with open("KNUH_Scenario_Map_RealData.html", "r", encoding="utf-8") as f:
        html_data = f.read()
    
    # 웹 화면에 HTML 렌더링
    components.html(html_data, height=800) # 스타일이 덮어씌워지므로 기본값은 800으로 둠
    
except FileNotFoundError:
    st.error("지도 HTML 파일을 찾을 수 없습니다. 파일명을 확인해주세요.")
