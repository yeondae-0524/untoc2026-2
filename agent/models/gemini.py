# agent/models/gemini.py

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


# 프로젝트에서 사용하는 5가지 도구 명세
def tool_declarations() -> List[Dict[str, Any]]:
    """Gemini에 전달할 도구 명세를 반환한다."""

    return [
        {
            "name": "read_file",
            "description": "Read the contents of an allowed file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "Path of the file to read."
                    }
                },
                "required": ["path"]
            }
        },
        {
            "name": "write_file",
            "description": "Write content to an allowed file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "Path of the file."
                    },
                    "content": {
                        "type": "STRING",
                        "description": "Content to write."
                    }
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "list_files",
            "description": "List files in an allowed directory.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "Directory path."
                    }
                },
                "required": ["path"]
            }
        },
        {
            "name": "create_file",
            "description": "Create a new file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "Path of the new file."
                    },
                    "content": {
                        "type": "STRING",
                        "description": "Initial file content."
                    }
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "delete_file",
            "description": "Delete an allowed file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {
                        "type": "STRING",
                        "description": "Path of the file to delete."
                    }
                },
                "required": ["path"]
            }
        }
    ]


class Gemini:
    """
    Gemini API 연동 클래스.

    transport를 전달하면 실제 API 대신 해당 함수를 사용한다.
    transport를 전달하지 않으면 Gemini REST API를 호출한다.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY 환경변수가 설정되지 않았습니다."
            )

        self.model_name = model
        self.transport = transport

        self.conversation_history: List[Dict[str, Any]] = []

        self.declarations = tool_declarations()

    def _send_request(
        self,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Gemini API 또는 테스트용 transport로 요청을 전송한다."""

        if self.transport is not None:
            try:
                response = self.transport(payload)

                if not isinstance(response, dict):
                    raise RuntimeError("invalid or blocked Gemini response")

                return response

            except RuntimeError:
                raise

            except Exception:
                raise RuntimeError(
                    "Gemini request failed"
                ) from None

        # 실제 Gemini REST API 호출
        encoded_key = urllib.parse.urlencode({
            "key": self.api_key
        })

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{self.model_name}:generateContent?"
            f"{encoded_key}"
        )

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30
            ) as result:
                response_data = result.read()

            response = json.loads(response_data)

            if not isinstance(response, dict):
                raise RuntimeError("invalid or blocked Gemini response")

            return response

        except Exception:
            # API 키나 원본 응답이 오류 메시지에 노출되지 않도록 한다.
            raise RuntimeError(
                "Gemini request failed"
            ) from None

    def _generate_content(
        self
    ) -> Dict[str, Any]:
        """현재 대화 이력을 사용하여 Gemini 응답을 생성한다."""

        payload = {
            "contents": self.conversation_history,
            "tools": [
                {
                    "functionDeclarations": self.declarations
                }
            ],
            "generationConfig": {
                "temperature": 0.0
            }
        }

        return self._send_request(payload)

    def _parse_response(
        self,
        response: Dict[str, Any]
    ) -> Turn:
        """Gemini 응답에서 텍스트와 모든 도구 호출을 추출한다."""

    def _parse_response(
        self,
        response: Dict[str, Any]
    ) -> Turn:

    from agent.agent import ToolCall, Turn


        
        candidates = response.get("candidates")

        if not candidates:
            raise RuntimeError(
                "invalid or blocked Gemini response"
            )

        candidate = candidates[0]

        if not isinstance(candidate, dict):
            raise RuntimeError(
                "invalid or blocked Gemini response"
            )

        content = candidate.get("content")

        if not isinstance(content, dict):
            raise RuntimeError(
                "invalid or blocked Gemini response"
            )

        parts = content.get("parts", [])

        if not isinstance(parts, list):
            raise RuntimeError(
                "invalid or blocked Gemini response"
            )

        # Gemini의 원본 응답을 수정하지 않고 보존한다.
        self.conversation_history.append(content)

        tool_calls = []
        text_parts = []

        for part in parts:

            if not isinstance(part, dict):
                continue

            function_call = part.get("functionCall")

            if function_call:
                tool_calls.append(
                    ToolCall(
                        name=function_call.get("name", ""),
                        arguments=function_call.get("args", {}),
                        id=function_call.get("id")
                    )
                )

            text = part.get("text")

            if text:
                text_parts.append(text)

        # 응답에 도구 호출이 있으면 모든 호출을 반환한다.
        if tool_calls:
            return Turn(
                text="".join(text_parts),
                tool_calls=tuple(tool_calls)
            )

        final_text = "".join(text_parts)

        if not final_text:
            raise RuntimeError(
                "invalid or blocked Gemini response"
            )

        return Turn(text=final_text)

    def start(self, prompt: str) -> Turn:
        """
        새로운 대화를 시작한다.

        이전 대화 이력을 초기화하고 사용자 프롬프트를 처리한다.
        """

        self.conversation_history = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]

        response = self._generate_content()

        return self._parse_response(response)

    def resume(
        self,
        tool_results: List[Tuple[ToolCall, Dict[str, Any]]]
    ) -> Turn:
        """
        도구 실행 결과를 Gemini에 전달하고 후속 응답을 생성한다.

        원본 Function Call ID를 유지하여
        각 Function Response와 연결한다.
        """

        if not self.conversation_history:
            raise RuntimeError(
                "No active Gemini conversation"
            )

        response_parts = []

        for call, result in tool_results:

            function_response = {
                "name": call.name,
                "response": result
            }

            # 원본 Function Call ID 보존
            if call.id is not None:
                function_response["id"] = call.id

            response_parts.append({
                "functionResponse": function_response
            })

        tool_content = {
            "role": "user",
            "parts": response_parts
        }

        self.conversation_history.append(tool_content)

        response = self._generate_content()

        return self._parse_response(response)
