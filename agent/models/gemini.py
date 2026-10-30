# agent/models/gemini.py
import os
from typing import List, Dict, Any, Tuple, Optional
from google import genai
from google.genai import types

class GeminiModel:
    """
    Gemini API 연동 모델 클래스.
    외부 연동 기준인 start()와 resume() 메서드를 제공합니다.
    """
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인해주세요.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        self.conversation_history: List[types.Content] = []
        self.tools_config: Optional[List[types.Tool]] = None

    def start(
        self, 
        prompt: str, 
        tool_declarations: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
        """
        대화 세션을 시작하고 사용자 프롬프트를 분석하여 Tool Call 여부를 감지합니다.
        
        Args:
            prompt: 사용자 입력 자연어
            tool_declarations: Tool Registry에서 제공한 도구 명세 리스트

        Returns:
            (tool_name, tool_arguments, text_response)
            - Tool Call 발생 시: (함수명, 인자 딕셔너리, None)
            - 일반 텍스트 응답 시: (None, None, 모델 응답 텍스트)
        """
        # 새 세션 시작을 위해 이전 대화 이력 초기화
        self.conversation_history = []

        # 1. Tool Registry 명세를 Gemini FunctionDeclaration 객체로 변환
        declarations = [
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters=tool.get("parameters", {"type": "OBJECT", "properties": {}})
            )
            for tool in tool_declarations
        ]
        self.tools_config = [types.Tool(function_declarations=declarations)]

        # 2. 사용자 입력 구성 및 히스토리 저장
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )
        self.conversation_history.append(user_content)

        # 3. 모델 호출 (결정론적 도구 호출을 위해 temperature=0.0 고정)
        config = types.GenerateContentConfig(
            tools=self.tools_config,
            temperature=0.0,
            system_instruction=(
                "너는 파일 시스템을 관리하는 AI 어시스턴트이다. "
                "사용자가 파일 관련 작업을 요청하면 반드시 등록된 적절한 도구를 호출하라."
            )
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=self.conversation_history,
            config=config
        )

        # 4. 모델 응답을 히스토리에 보존
        if response.candidates and response.candidates[0].content:
            self.conversation_history.append(response.candidates[0].content)

        # 5. Tool Call 추출 및 반환
        if response.function_calls:
            call = response.function_calls[0]
            return call.name, dict(call.args), None

        return None, None, response.text

    def resume(self, tool_name: str, tool_result: Dict[str, Any]) -> str:
        """
        B팀 게이트웨이의 판정 및 실행 결과(ALLOW/BLOCK)를 주입하여 최종 응답을 완성합니다.
        
        Args:
            tool_name: 호출되었던 도구명
            tool_result: 게이트웨이 결과 딕셔너리

        Returns:
            최종 사용자 응답 자연어 텍스트
        """
        if not self.conversation_history:
            raise RuntimeError("진행 중인 세션이 없습니다. start()를 먼저 호출해야 합니다.")

        # 1. Function Response Part 생성 후 대화 이력에 추가 (role="tool")
        tool_response_part = types.Part.from_function_response(
            name=tool_name,
            response=tool_result
        )
        tool_content = types.Content(
            role="tool",
            parts=[tool_response_part]
        )
        self.conversation_history.append(tool_content)

        # 2. 업데이트된 컨텍스트로 모델의 최종 자연어 요약 생성
        config = types.GenerateContentConfig(
            tools=self.tools_config,
            temperature=0.0
        )

        final_response = self.client.models.generate_content(
            model=self.model_name,
            contents=self.conversation_history,
            config=config
        )

        if final_response.candidates and final_response.candidates[0].content:
            self.conversation_history.append(final_response.candidates[0].content)

        return final_response.text
