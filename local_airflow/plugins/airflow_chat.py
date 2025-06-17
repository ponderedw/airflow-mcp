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
from datetime import datetime, timedelta
import uuid
from sqlalchemy import Column, String, DateTime, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Blueprint for the chat plugin
bp = Blueprint(
    "airflow_chat",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static/airflow_chat_plugin",
)

# Database model for storing chat sessions
Base = declarative_base()


class ChatSession(Base):
    __tablename__ = 'airflow_chat_sessions'
    
    conversation_id = Column(String(255), primary_key=True)
    cookies_data = Column(Text)  # JSON string of cookies
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'conversation_id': self.conversation_id,
            'cookies_data': self.cookies_data,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None
        }


class ChatSessionManager:
    def __init__(self):
        # Use Airflow's metadata database
        self.engine = settings.engine
        self.Session = sessionmaker(bind=self.engine)
        
        # Create the table if it doesn't exist
        try:
            Base.metadata.create_all(self.engine)
        except Exception as e:
            print(f"Error creating chat sessions table: {e}")
    
    def cleanup_old_sessions(self):
        """Remove sessions older than 24 hours"""
        try:
            session = self.Session()
            cutoff_time = datetime.utcnow() - timedelta(minutes=20)
            
            deleted_count = session.query(ChatSession).filter(
                ChatSession.created_at < cutoff_time
            ).delete()
            
            session.commit()
            session.close()
            
            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old chat sessions")
                
        except SQLAlchemyError as e:
            print(f"Error cleaning up old sessions: {e}")
            if session:
                session.rollback()
                session.close()
    
    def store_session(self, conversation_id, cookies_dict):
        """Store or update a chat session"""
        try:
            session = self.Session()
            
            # Convert cookies to JSON string
            cookies_json = json.dumps(dict(cookies_dict)) if cookies_dict else "{}"
            
            # Check if session exists
            existing_session = session.query(ChatSession).filter(
                ChatSession.conversation_id == conversation_id
            ).first()
            
            if existing_session:
                # Update existing session
                existing_session.cookies_data = cookies_json
                existing_session.last_accessed = datetime.utcnow()
            else:
                # Create new session
                new_session = ChatSession(
                    conversation_id=conversation_id,
                    cookies_data=cookies_json,
                    created_at=datetime.utcnow(),
                    last_accessed=datetime.utcnow()
                )
                session.add(new_session)
            
            session.commit()
            session.close()
            return True
            
        except SQLAlchemyError as e:
            print(f"Error storing session: {e}")
            if session:
                session.rollback()
                session.close()
            return False
    
    def get_session(self, conversation_id):
        """Retrieve a chat session"""
        try:
            session = self.Session()
            
            chat_session = session.query(ChatSession).filter(
                ChatSession.conversation_id == conversation_id
            ).first()
            
            if chat_session:
                # Update last accessed time
                chat_session.last_accessed = datetime.utcnow()
                session.commit()
                
                # Parse cookies from JSON
                cookies_dict = json.loads(chat_session.cookies_data) if chat_session.cookies_data else {}
                
                session.close()
                return cookies_dict
            else:
                session.close()
                return None
                
        except SQLAlchemyError as e:
            print(f"Error retrieving session: {e}")
            if session:
                session.close()
            return None
    
    def delete_session(self, conversation_id):
        """Delete a specific chat session"""
        try:
            session = self.Session()
            
            deleted_count = session.query(ChatSession).filter(
                ChatSession.conversation_id == conversation_id
            ).delete()
            
            session.commit()
            session.close()
            
            return deleted_count > 0
            
        except SQLAlchemyError as e:
            print(f"Error deleting session: {e}")
            if session:
                session.rollback()
                session.close()
            return False
    
    def get_active_sessions_count(self):
        """Get count of active sessions (within 24 hours)"""
        try:
            session = self.Session()
            cutoff_time = datetime.utcnow() - timedelta(hours=24)
            
            count = session.query(ChatSession).filter(
                ChatSession.created_at >= cutoff_time
            ).count()
            
            session.close()
            return count
            
        except SQLAlchemyError as e:
            print(f"Error getting session count: {e}")
            if session:
                session.close()
            return 0

def admin_only(f):
    """Decorator to restrict access to Admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated
        if not current_user or not hasattr(current_user, 'roles') or not current_user.roles:
            return jsonify({"error": "Authentication required"}), 401
        
        users_roles = [role.name for role in current_user.roles]
        approved_roles = ["Admin"]  # Adjust roles as needed
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
        self.backend_url = os.environ.get('BACKEND_URL', "http://fastapi:8080")
        self.access_token = os.environ.get('FAST_API_ACCESS_SECRET_TOKEN', 'ThisIsATempAccessTokenForLocalEnvs.ReplaceInProd')
        self.session_manager = ChatSessionManager()
        
        # Perform cleanup on initialization
        self.session_manager.cleanup_old_sessions()
    
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
            
            # Store session cookies in database
            success = self.session_manager.store_session(conversation_id, response.cookies)
            
            if success:
                print(f"Initialized and stored chat session: {conversation_id}")
                return True, response.cookies
            else:
                print(f"Failed to store session in database: {conversation_id}")
                return False, None
            
        except requests.RequestException as e:
            print(f"Failed to initialize chat session: {e}")
            return False, None
        except Exception as e:
            print(f"Unexpected error initializing session: {e}")
            return False, None
    
    def stream_chat_response(self, message, conversation_id=None):
        """Stream response from FastAPI backend"""
        try:
            # Clean up old sessions periodically (every request)
            self.session_manager.cleanup_old_sessions()
            
            # Get session cookies from database
            cookies_dict = self.session_manager.get_session(conversation_id)
            
            if cookies_dict is None:
                # Initialize session if not found
                success, cookies = self.initialize_chat_session(conversation_id)
                if not success:
                    yield {
                        "content": "Failed to initialize chat session. Please try again.",
                        "conversation_id": conversation_id,
                        "timestamp": datetime.now().isoformat(),
                        "error": True
                    }
                    return
                cookies_dict = dict(cookies)
            
            # Convert dict back to requests.cookies.RequestsCookieJar if needed
            # For requests library, we can pass cookies as dict
            
            # Stream chat response
            with requests.post(
                f"{self.backend_url}/chat/ask",
                stream=True,
                headers=self.get_headers(),
                json={'message': message},
                cookies=cookies_dict,
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
        print(f'Clearing session: {conversation_id}')
        return self.session_manager.delete_session(conversation_id)
    
    def get_session_stats(self):
        """Get statistics about active sessions"""
        return {
            'active_sessions': self.session_manager.get_active_sessions_count()
        }

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
    
    @expose("/api/clear_session", methods=["POST"])
    @admin_only
    @failure_tolerant
    def clear_session(self):
        """Clear a specific chat session"""
        data = request.get_json()
        conversation_id = data.get("conversation_id")
        
        if not conversation_id:
            return jsonify({"error": "Conversation ID is required"}), 400
        
        try:
            success = self.llm_agent.clear_session(conversation_id)
            
            if success:
                return jsonify({
                    "message": "Session cleared successfully",
                    "conversation_id": conversation_id
                })
            else:
                return jsonify({
                    "error": "Session not found or could not be cleared"
                }), 404
                
        except Exception as e:
            return jsonify({
                "error": f"Error clearing session: {str(e)}"
            }), 500
    
    @expose("/api/session_stats", methods=["GET"])
    @admin_only
    @failure_tolerant
    def session_stats(self):
        """Get session statistics"""
        try:
            stats = self.llm_agent.get_session_stats()
            return jsonify(stats)
        except Exception as e:
            return jsonify({
                "error": f"Error getting session stats: {str(e)}"
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