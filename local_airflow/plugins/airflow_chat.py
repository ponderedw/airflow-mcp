from airflow import settings
from airflow.plugins_manager import AirflowPlugin
from flask import Blueprint, request, jsonify, Response, stream_template
from flask_login import current_user
from flask_appbuilder import expose, BaseView as AppBuilderBaseView
from functools import wraps
import json
import time
import requests
import os
from datetime import datetime
import uuid

# Blueprint for the chat plugin
bp = Blueprint(
    "airflow_chat",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static/airflow_chat_plugin",
)

def admin_only(f):
    """Decorator to restrict access to Admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated
        if not current_user or not hasattr(current_user, 'roles') or not current_user.roles:
            return jsonify({"error": "Authentication required"}), 401
        
        users_roles = [role.name for role in current_user.roles]
        approved_roles = ["Admin", "User", "Op", "Viewer"]  # Adjust roles as needed
        if not any(role in users_roles for role in approved_roles):
            return jsonify({"error": "Access denied"}), 403
        return f(*args, **kwargs)
    return decorated_function

def failure_tolerant(f):
    """Decorator for error handling."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            print(f"CHAT PLUGIN FAILURE: {e}")
            return jsonify({"error": "Something went wrong"}), 500
    return decorated_function

class LLMChatAgent:
    def __init__(self):
        self.backend_url = "http://fastapi:8080"
        self.access_token = os.environ.get('FAST_API_ACCESS_SECRET_TOKEN', 'ThisIsATempAccessTokenForLocalEnvs.ReplaceInProd')
        self.sessions = {}  # Store session info per conversation
    
    def get_headers(self):
        return {
            "x-access-token": self.access_token
        }
    
    def initialize_chat_session(self, conversation_id):
        """Initialize a new chat session"""
        try:
            response = requests.post(
                f"{self.backend_url}/chat/new",
                headers=self.get_headers(),
                timeout=10
            )
            response.raise_for_status()
            
            # Store session cookies for this conversation
            self.sessions[conversation_id] = {
                'cookies': response.cookies,
                'initialized': True
            }
            
            return True, response.cookies
            
        except requests.RequestException as e:
            print(f"Failed to initialize chat session: {e}")
            return False, None
        except Exception as e:
            print(f"Unexpected error initializing session: {e}")
            return False, None
    
    def stream_chat_response(self, message, conversation_id=None):
        print(f'sessions: {self.sessions}')
        """Stream response from FastAPI backend"""
        try:
            # if not conversation_id:
            #     conversation_id = str(uuid.uuid4())
            
            # Initialize session if not already done
            if conversation_id not in self.sessions or not self.sessions[conversation_id].get('initialized'):
                success, cookies = self.initialize_chat_session(conversation_id)
                if not success:
                    yield {
                        "content": "Failed to initialize chat session. Please try again.",
                        "conversation_id": conversation_id,
                        "timestamp": datetime.now().isoformat(),
                        "error": True
                    }
                    return
            
            # Get session cookies
            cookies = self.sessions[conversation_id].get('cookies', {})
            
            # Stream chat response
            with requests.post(
                f"{self.backend_url}/chat/ask",  # Adjust endpoint if needed
                stream=True,
                headers=self.get_headers(),
                json={'message': message},
                cookies=cookies,
                timeout=30
            ) as response:
                
                response.raise_for_status()
                
                # Stream response chunks
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        try:
                            # Decode the chunk
                            content = str(chunk, encoding="utf-8")
                            
                            # Yield the content as structured response
                            yield {
                                "content": content,
                                "conversation_id": conversation_id,
                                "timestamp": datetime.now().isoformat()
                            }
                            
                        except UnicodeDecodeError:
                            # Skip chunks that can't be decoded
                            continue
                        except Exception as e:
                            print(f"Error processing chunk: {e}")
                            continue
                
        except requests.RequestException as e:
            # Fallback for connection errors
            yield {
                "content": f"Connection error: {str(e)}. Please check if the FastAPI service is running.",
                "conversation_id": conversation_id,
                "timestamp": datetime.now().isoformat(),
                "error": True
            }
        except Exception as e:
            yield {
                "content": f"An unexpected error occurred: {str(e)}",
                "conversation_id": conversation_id,
                "timestamp": datetime.now().isoformat(),
                "error": True
            }
    
    def clear_session(self, conversation_id):
        """Clear session data for a conversation"""
        print('clear_session')
        if conversation_id in self.sessions:
            del self.sessions[conversation_id]

class AirflowChatView(AppBuilderBaseView):
    default_view = "chat_interface"
    route_base = "/airflow_chat"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.llm_agent = LLMChatAgent()
    
    @expose("/")
    @admin_only
    @failure_tolerant
    def chat_interface(self):
        """Main chat interface"""
        return self.render_template("chat_interface.html")
    
    @expose("/api/chat", methods=["POST"])
    @admin_only
    @failure_tolerant
    def chat_api(self):
        """API endpoint for chat messages"""
        data = request.get_json()
        message = data.get("message", "").strip()
        conversation_id = data.get("conversation_id")
        print(f'conversation_id: {conversation_id}')
        
        if not message:
            return jsonify({"error": "Message is required"}), 400
        
        # Return streaming response
        def generate():
            try:
                for chunk in self.llm_agent.stream_chat_response(
                    message, 
                    conversation_id
                ):
                    yield f"data: {json.dumps(chunk)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                error_chunk = {
                    "content": f"Error: {str(e)}",
                    "conversation_id": conversation_id or str(uuid.uuid4()),
                    "timestamp": datetime.now().isoformat(),
                    "error": True
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
        
        return Response(
            generate(),
            mimetype="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*"
            }
        )

    
    @expose("/api/new_chat", methods=["POST"])
    @admin_only
    @failure_tolerant
    def new_chat(self):
        """Initialize a new chat session"""
        conversation_id = str(uuid.uuid4())
        
        try:
            success, cookies = self.llm_agent.initialize_chat_session(conversation_id)
            
            if success:
                return jsonify({
                    "conversation_id": conversation_id,
                    "status": "initialized",
                    "message": "New chat session created"
                })
            else:
                return jsonify({
                    "error": "Failed to initialize chat session"
                }), 500
                
        except Exception as e:
            return jsonify({
                "error": f"Error creating new chat: {str(e)}"
            }), 500

# Create the view instance
chat_view = AirflowChatView()

# Package for Airflow
chat_package = {
    "name": "AI Chat Assistant",
    "category": "Tools",
    "view": chat_view,
}

class AirflowChatPlugin(AirflowPlugin):
    name = "airflow_chat_plugin"
    hooks = []
    macros = []
    flask_blueprints = [bp]
    appbuilder_views = [chat_package]
    appbuilder_menu_items = []