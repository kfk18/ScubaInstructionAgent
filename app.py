import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from tavily import TavilyClient
import datetime

# --- 設定と初期化 ---
st.set_page_config(page_title="Diving Agent 🤿", layout="wide")

# シークレットからAPIキーを読み込む（ハードコーディング回避！）
try:
    GOOGLE_API_KEY = st.secrets["general"]["GOOGLE_API_KEY"]
    TAVILY_API_KEY = st.secrets["general"]["TAVILY_API_KEY"]
except FileNotFoundError:
    st.error("secrets.tomlファイルが見つかりません。APIキーを設定してください。")
    st.stop()

# Geminiの設定
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-3-flash-preview') # 高速で軽量なモデルを選択

# Tavilyの設定
tavily = TavilyClient(api_key=TAVILY_API_KEY)

# ダイビングスポットの座標定義（CSVから読み込み）
try:
    DIVING_SPOTS = pd.read_csv("diving_spots.csv").set_index("name").to_dict("index")
except Exception as e:
    st.error(f"スポット情報の読み込みに失敗しました: {e}")
    st.stop()

# WMO天気コードの簡易マッピング
WEATHER_CODE_MAP = {
    0: "快晴", 1: "晴れ", 2: "一部曇り", 3: "曇り",
    45: "霧", 48: "着氷性の霧",
    51: "霧雨(軽)", 53: "霧雨(中)", 55: "霧雨(強)",
    61: "雨(軽)", 63: "雨(中)", 65: "雨(強)",
    80: "にわか雨(軽)", 81: "にわか雨(中)", 82: "にわか雨(強)",
    # 他のコードは必要に応じて追加
}

# --- 関数定義 ---

def get_meteo_data(lat, lon, date):
    """Open-Meteoから気象・海況データを取得する"""
    url = "https://marine-api.open-meteo.com/v1/marine"
    
    # 必要なパラメータ
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date,
        "end_date": date,
        "hourly": [
            "wave_height", "wave_direction", "wave_period", 
            "swell_wave_height", "swell_wave_direction"
        ],
        "timezone": "Asia/Tokyo"
    }
    
    # 天気情報は別のエンドポイント(Forecast API)にあるため併用
    forecast_url = "https://api.open-meteo.com/v1/forecast"
    forecast_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date,
        "end_date": date,
        "hourly": [
            "temperature_2m", "precipitation", "weather_code", 
            "wind_speed_10m", "wind_direction_10m"
        ],
        "timezone": "Asia/Tokyo"
    }

    try:
        marine_res = requests.get(url, params=params).json()
        weather_res = requests.get(forecast_url, params=forecast_params).json()
        
        # データの結合処理
        hourly_marine = marine_res.get('hourly', {})
        hourly_weather = weather_res.get('hourly', {})
        
        # 時間軸
        times = hourly_marine.get('time', [])
        
        df = pd.DataFrame({
            "time": pd.to_datetime(times),
            "気温(°C)": hourly_weather.get('temperature_2m', []),
            "降水量(mm)": hourly_weather.get('precipitation', []),
            "風速(km/h)": hourly_weather.get('wind_speed_10m', []),
            "風向(°)": hourly_weather.get('wind_direction_10m', []),
            "天気コード": hourly_weather.get('weather_code', []),
            "波高(m)": hourly_marine.get('wave_height', []),
            "波向(°)": hourly_marine.get('wave_direction', []),
            "波の強さ/周期(s)": hourly_marine.get('wave_period', []),
            "うねり高(m)": hourly_marine.get('swell_wave_height', []),
        })
        
        # 天気コードを日本語に変換
        df["天気"] = df["天気コード"].map(lambda x: WEATHER_CODE_MAP.get(x, f"不明({x})"))
        
        return df
    except Exception as e:
        st.error(f"気象データの取得に失敗しました: {e}")
        return None

def search_marine_life(location, date):
    """Tavilyで検索し、Geminiで生物情報を抽出する"""
    month = date.month
    query = f"{location} ダイビング 生物 見られる魚 {month}月 マクロ ワイド"
    
    with st.spinner(f"🔍 '{query}' で情報を収集中..."):
        try:
            # Tavilyで検索
            search_result = tavily.search(query=query, search_depth="advanced", max_results=3)
            context = "\n".join([res['content'] for res in search_result['results']])
            
            # Geminiに整理させる
            prompt = f"""
            以下の検索結果に基づいて、{location}の{month}月にダイビングで見られる可能性が高い海洋生物をリストアップしてください。
            出力は以下のフォーマットのみを含むマークダウンの箇条書きにしてください。余計な前置きは不要です。

            - **生物名**: 特徴や見どころ（一言で）

            検索結果:
            {context}
            """
            
            response = model.generate_content(prompt)
            return response.text
            
        except Exception as e:
            st.error(f"生物情報の検索に失敗しました: {e}")
            return "情報の取得に失敗しました。"

# --- UI構築 ---

st.title("🤿 Scuba Diving Agent")
st.markdown("指定した地域の気象・海況情報と、今の時期に見られる生物をお知らせします。")

# 入力エリア
with st.container():
    st.subheader("条件設定")
    col1, col2 = st.columns(2)
    with col1:
        selected_spot_name = st.selectbox("エリアを選択", list(DIVING_SPOTS.keys()))
    with col2:
        selected_date = st.date_input("日付を選択", datetime.date.today())

    if selected_date < datetime.date.today() - datetime.timedelta(days=7):
        st.warning("⚠️ 過去のデータはOpen-Meteoの仕様により取得できない場合があります（Historical APIが必要になります）")

    start_btn = st.button("情報を取得する", type="primary")

# メイン処理
if start_btn:
    coords = DIVING_SPOTS[selected_spot_name]
    
    # データを取得
    df = get_meteo_data(coords['lat'], coords['lon'], selected_date)
    bio_info = search_marine_life(selected_spot_name, selected_date)
    
    # session_stateに保存
    st.session_state["display_data"] = {
        "spot_name": selected_spot_name,
        "date": selected_date,
        "df": df,
        "bio_info": bio_info
    }

if "display_data" in st.session_state:
    data = st.session_state["display_data"]
    
    # 1. 気象・海況情報の表示
    st.header(f"🌊 {data['spot_name']} の海況・気象予報 ({data['date']})")
    
    df = data["df"]
    
    if df is not None:
        # 概要指標（お昼12時のデータを代表値として表示）
        target_hour = 12
        # データフレームから該当時間を抽出（近似）
        midday_data = df[df['time'].dt.hour == target_hour].iloc[0] if not df[df['time'].dt.hour == target_hour].empty else df.iloc[len(df)//2]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("天気 (12:00)", midday_data["天気"])
        col2.metric("気温 (12:00)", f"{midday_data['気温(°C)']} °C")
        col3.metric("風速", f"{midday_data['風速(km/h)']} km/h", f"向: {midday_data['風向(°)']}°")
        col4.metric("波高", f"{midday_data['波高(m)']} m", f"周期: {midday_data['波の強さ/周期(s)']}s")

        st.markdown("---")
        
        # グラフ表示
        st.subheader("📊 時系列データ")
        
        tab1, tab2 = st.tabs(["波・うねり", "天気・風"])
        
        with tab1:
            st.markdown("#### 波の高さとうねり")
            st.line_chart(df.set_index("time")[["波高(m)", "うねり高(m)"]])
            st.caption("※うねりが高いとエントリーが難しくなる可能性があります。")

        with tab2:
            st.markdown("#### 風速と気温")
            st.line_chart(df.set_index("time")[["風速(km/h)", "気温(°C)"]])

    # 2. 生物情報の表示
    st.markdown("---")
    st.header(f"🐠 {data['date'].month}月に期待できる生物")
    
    st.markdown(data["bio_info"])
    
    # 最後にAIからのコメント風
    st.info("💡 Open-Meteoの予報とWeb検索結果に基づいています。現地のショップ情報も必ず確認してくださいね！")