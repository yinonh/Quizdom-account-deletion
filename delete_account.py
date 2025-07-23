import streamlit as st
from datetime import datetime
import firebase_admin
from PIL import Image
from firebase_admin import credentials, firestore, auth
from dotenv import load_dotenv
import os
import requests
import io
import base64

st.set_page_config(
    page_title="Quizdom - Ultimate Trivia Experience",
    page_icon="./icon_small.png",
    layout="wide"
)


# Initialize Firebase Admin SDK
def initialize_firebase():
    if 'firebase_db' not in st.session_state:
        if not firebase_admin._apps:
            cred_dict = dict(st.secrets["firebase_service_account"])
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        st.session_state.firebase_db = firestore.client()

    return st.session_state.firebase_db


FIREBASE_WEB_API_KEY = st.secrets["FIREBASE_WEB_API_KEY"]
FIREBASE_AUTH_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"


def authenticate_user(email, password):
    """Authenticate user using Firebase Web API"""
    try:
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }

        response = requests.post(FIREBASE_AUTH_URL, json=payload)

        if response.status_code == 200:
            user_data = response.json()
            return {
                "success": True,
                "uid": user_data["localId"],
                "email": user_data["email"],
                "token": user_data["idToken"]
            }
        else:
            error_data = response.json()
            error_message = error_data.get("error", {}).get("message", "Authentication failed")
            return {
                "success": False,
                "error": error_message
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_user_info(db, user_id):
    """Get user information from Firestore"""
    try:
        user_doc = db.collection("users").document(user_id).get()
        if user_doc.exists:
            return user_doc.to_dict()
        return None
    except Exception as e:
        st.error(f"Error getting user info: {e}")
        return None


def delete_user_from_auth(user_id):
    """Delete user from Firebase Authentication"""
    try:
        auth.delete_user(user_id)
        return True
    except auth.UserNotFoundError:
        st.warning("User not found in Firebase Authentication, but will still clean up Firestore data.")
        return True
    except Exception as e:
        st.error(f"Error deleting user from Firebase Auth: {e}")
        return False


def delete_user_firestore_data(db, user_id):
    """Delete all user data from Firestore collections"""
    try:
        deleted_collections = []

        # Delete from users collection
        user_doc = db.collection("users").document(user_id)
        if user_doc.get().exists:
            user_doc.delete()
            deleted_collections.append("users")

        # Delete from userStatistics collection
        stats_doc = db.collection("userStatistics").document(user_id)
        if stats_doc.get().exists:
            stats_doc.delete()
            deleted_collections.append("userStatistics")

        # Optional: Delete from other collections that might contain user data
        other_collections = ["userPreferences", "gameHistory", "userAchievements"]

        for collection_name in other_collections:
            try:
                doc = db.collection(collection_name).document(user_id)
                if doc.get().exists:
                    doc.delete()
                    deleted_collections.append(collection_name)
            except Exception:
                pass  # Collection might not exist

        return deleted_collections

    except Exception as e:
        raise Exception(f"Failed to delete user data from Firestore: {e}")


def delete_user_related_documents(db, user_id):
    """Delete documents in collections that reference the user"""
    try:
        deleted_docs = 0

        # Delete from triviaRooms where user is involved
        try:
            trivia_rooms = db.collection("triviaRooms").where("createdBy", "==", user_id).get()
            for room in trivia_rooms:
                room.reference.delete()
                deleted_docs += 1
        except Exception:
            pass

        # Delete from availablePlayers
        try:
            available_players = db.collection("availablePlayers").where("userId", "==", user_id).get()
            for player in available_players:
                player.reference.delete()
                deleted_docs += 1
        except Exception:
            pass

        return deleted_docs

    except Exception as e:
        st.warning(f"Some user-related documents might not have been deleted: {e}")
        return 0


def complete_user_deletion(db, user_id):
    """Complete user deletion process"""
    try:
        # Step 1: Delete from Firebase Authentication
        auth_deleted = delete_user_from_auth(user_id)

        # Step 2: Delete user data from Firestore
        deleted_collections = delete_user_firestore_data(db, user_id)

        # Step 3: Delete user-related documents
        related_docs_deleted = delete_user_related_documents(db, user_id)

        return {
            "success": True,
            "auth_deleted": auth_deleted,
            "collections_deleted": deleted_collections,
            "related_docs_deleted": related_docs_deleted
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def img_to_base64(file_path):
    img = Image.open(file_path)
    byte_arr = io.BytesIO()
    img.save(byte_arr, format='PNG')
    return base64.b64encode(byte_arr.getvalue()).decode()


def home_page():
    """Display the home page introducing Quizdom"""

    # Hero Section
    col1, col2 = st.columns([2, 3])

    with col1:
        st.markdown(
            f"""
            <div style="
                display: flex;
                justify-content: center;
                align-items: center;
                height: 40vh;
                min-height: 300px;
            ">
                <img src="data:image/png;base64,{img_to_base64('icon_transparent.png')}" 
                     width="250" 
                     style="display: block;">
            </div>
            """,
            unsafe_allow_html=True
        )

    with col2:
        st.markdown("""
        # 🏆 Welcome to Quizdom
        ## *The Ultimate Trivia Experience*

        **Challenge your mind, compete with friends, and become the trivia champion!**

        Quizdom is an engaging multiplayer trivia game that brings knowledge and fun together. 
        Test your skills across multiple categories, compete in real-time battles, and climb 
        the leaderboards to prove you're the ultimate quiz master.
        """)


        st.link_button(label="📱 Download on Google Play", type="primary",url="https://play.google.com/store/apps/details?id=com.yinonhdev.quizdom", use_container_width=True)

    st.markdown("---")

    # Features Section
    st.markdown("## 🌟 Game Features")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("""
        ### 🎯 Multiple Game Modes
        - **Solo Mode**: Practice and improve your skills
        - **Duel Mode**: 1v1 real-time battles
        - **Group Mode**: Compete with multiple players
        - **Bot Challenges**: Test your skills against AI
        """)

    with col2:
        st.markdown("""
        ### 📚 Diverse Categories
        - Science & Nature
        - Entertainment & Movies
        - Sports & Recreation
        - History & Geography
        - Art & Literature
        - And many more!
        """)

    with col3:
        st.markdown("""
        ### 🏅 Achievement System
        - Unlock special achievements
        - Daily login rewards
        - Streak bonuses
        - Leaderboard rankings
        - Coin collection system
        """)

    st.markdown("---")

    # How It Works Section
    st.markdown("## 🎲 How It Works")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("""
        ### 1️⃣ Choose Mode
        Select your preferred game mode and difficulty level
        """)

    with col2:
        st.markdown("""
        ### 2️⃣ Pick Category
        Choose from dozens of trivia categories
        """)

    with col3:
        st.markdown("""
        ### 3️⃣ Compete
        Answer questions quickly and accurately
        """)

    with col4:
        st.markdown("""
        ### 4️⃣ Win Rewards
        Earn coins, achievements, and climb leaderboards
        """)

    st.markdown("---")

    # Stats Section
    # st.markdown("## 📊 Join the Community")
    #
    # col1, col2, col3, col4 = st.columns(4)
    #
    # with col1:
    #     st.metric("Active Players", "10,000+", "↗️ Growing")
    # with col2:
    #     st.metric("Questions Available", "50,000+", "🎯 Diverse")
    # with col3:
    #     st.metric("Categories", "25+", "📚 Topics")
    # with col4:
    #     st.metric("Daily Challenges", "New", "🔥 Fresh")
    #
    # st.markdown("---")

    # Account Management Section
    st.markdown("## ⚙️ Account Management")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("""
        ### Privacy & Account Control

        We respect your privacy and give you full control over your account data. 
        You can manage your account settings directly in the app, or use our web portal 
        for account deletion if needed.

        **Account Features:**
        - Profile customization
        - Privacy settings
        - Data export options
        - Secure authentication
        - Account deletion
        """)

    with col2:
        st.markdown("### 🔐 Account Actions")
        if st.button("🗑️ Delete Account", type="secondary", use_container_width=True):
            st.session_state.page = "deletion"
            st.rerun()

        if st.button("📧 Contact Support", use_container_width=True):
            st.info("📬 Support: yinon.h21+quizdom@gmail.com")

    st.markdown("---")

    # Footer
    st.markdown("""
    <div style="text-align: center; padding: 2rem 0; color: #666;">
        <p><strong>Quizdom</strong> - Challenge Your Mind, Expand Your Knowledge</p>
        <p>© 2025 Quizdom. All rights reserved.</p>
        <p>
            <a href="https://doc-hosting.flycricket.io/quizdom-privacy-policy/e95e1934-c14d-4c56-80ee-9a7dd0373cca/privacy" target="_blank" style="color: #00AFFF; text-decoration: none;">Privacy Policy</a> | 
            <!-- <a href="#terms" style="color: #00AFFF; text-decoration: none;">Terms of Service</a> | -->
            <a href="mailto:yinon.h21+quizdom@gmail.com" style="color: #00AFFF; text-decoration: none;">Contact Support</a>
        </p>
    </div>
    """, unsafe_allow_html=True)


def login_page():
    """Display login page"""
    # col1, col2 = st.columns([1, 4])
    #
    # with col1:
    #     st.markdown(
    #         f"""
    #             <div style="
    #                 display: flex;
    #                 justify-content: center;
    #                 align-items: center;
    #                 height: 30vh;
    #                 min-height: 200px;
    #                 margin-top: -50px;
    #             ">
    #                 <img src="data:image/png;base64,{img_to_base64('icon_transparent.png')}"
    #                      width="150"
    #                      style="display: block;">
    #             </div>
    #             """,
    #         unsafe_allow_html=True
    #     )
    #
    # with col2:
    st.title("Quizdom Account Deletion Portal")

    # IMPORTANT NOTICE
    st.empty()
    st.error(
        "**🚨 Important:** You must be **logged out** of the main app before deleting your account from here.\n\n"
        "If you still have access to your account, it is **recommended** to delete your account through the **Profile** screen in the app."
    )
    st.markdown("---")
    st.write("Please log in with your trivia game account to request deletion.")

    # Login form
    with st.form("login_form"):
        st.subheader("Login to Your Account")
        email = st.text_input("Email Address")
        password = st.text_input("Password", type="password")
        login_button = st.form_submit_button("Login")

        if login_button:
            if not email or not password:
                st.error("Please enter both email and password")
                return

            with st.spinner("Authenticating..."):
                auth_result = authenticate_user(email, password)

                if auth_result["success"]:
                    # Store user session
                    st.session_state.authenticated = True
                    st.session_state.user_uid = auth_result["uid"]
                    st.session_state.user_email = auth_result["email"]
                    st.session_state.user_token = auth_result["token"]
                    st.rerun()
                else:
                    st.error(f"Login failed: {auth_result['error']}")

    # Information section
    st.markdown("---")
    st.subheader("ℹ️ Account Deletion Information")
    st.markdown("""
    **What happens when you delete your account:**
    - Your user profile and account information will be permanently deleted
    - All game statistics and history will be removed
    - Any trivia rooms you created will be deleted
    - You will not be able to recover your account or data
    - This action is irreversible

    **Before you proceed:**
    - Make sure you really want to delete your account
    - Consider if there's any game data you want to remember
    - You can create a new account later if you change your mind
    """)


def deletion_page():
    """Display account deletion page for authenticated users"""
    db = initialize_firebase()

    col1, col2 = st.columns([1, 5])

    with col1:
        st.markdown(
            f"""
                    <div style="
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 10vh;
                        min-height: 200px;
                        margin-top: -50px;
                    ">
                        <img src="data:image/png;base64,{img_to_base64('icon_transparent.png')}" 
                             width="150" 
                             style="display: block;">
                    </div>
                    """,
            unsafe_allow_html=True
        )

    with col2:
        st.title("Delete Your Account")
    st.write(f"Logged in as: **{st.session_state.user_email}**")

    # Navigation buttons
    col_nav1, col_nav2 = st.columns([1, 1])
    with col_nav1:
        if st.button("← Back to Home", type="secondary"):
            st.session_state.page = "home"
            # Clear auth session
            for key in ['authenticated', 'user_uid', 'user_email', 'user_token']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    with col_nav2:
        if st.button("Logout", type="secondary"):
            for key in ['authenticated', 'user_uid', 'user_email', 'user_token']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    # Get and display user information
    user_info = get_user_info(db, st.session_state.user_uid)

    if user_info:
        st.subheader("Your Account Information")
        col1, col2 = st.columns(2)

        with col1:
            st.write(f"**Name:** {user_info.get('name', 'N/A')}")

        with col2:
            created_at = user_info.get('createdAt')
            if created_at:
                st.write(f"**Account Created:** {created_at}")

            last_login = user_info.get('lastLogin')
            if last_login:
                try:
                    dt = datetime.fromisoformat(str(last_login))
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M")
                    st.write(f"**Last Login:** {formatted_time}")
                except Exception as e:
                    st.write(f"**Last Login:** {last_login}")
                    st.warning(f"Couldn't format timestamp: {e}")

    st.markdown("---")

    # Deletion confirmation section
    st.subheader("⚠️ Permanent Account Deletion")
    st.error("This action cannot be undone. Your account and all associated data will be permanently deleted.")

    # Confirmation checkboxes
    understand_permanent = st.checkbox("I understand this deletion is permanent and irreversible")
    understand_data_loss = st.checkbox("I understand all my game data, statistics, and progress will be lost")
    understand_no_recovery = st.checkbox("I understand my account cannot be recovered after deletion")

    # Final confirmation
    if understand_permanent and understand_data_loss and understand_no_recovery:
        delete_button = st.button(
            "🗑️ DELETE MY ACCOUNT PERMANENTLY",
            type="primary",
        )

        if delete_button:
            with st.spinner("Deleting your account... Please wait."):
                result = complete_user_deletion(db, st.session_state.user_uid)

                if result["success"]:
                    st.success("✅ Your account has been successfully deleted!")

                    # Show deletion summary
                    st.subheader("Deletion Summary:")
                    col1, col2 = st.columns(2)

                    with col1:
                        st.write(f"**Authentication:** {'✅ Deleted' if result['auth_deleted'] else '❌ Failed'}")
                        st.write(f"**Collections deleted:** {len(result['collections_deleted'])}")
                        if result['collections_deleted']:
                            for collection in result['collections_deleted']:
                                st.write(f"  - {collection}")

                    with col2:
                        st.write(f"**Related documents deleted:** {result['related_docs_deleted']}")

                    st.balloons()

                    # Clear session after successful deletion
                    st.info("You will be logged out automatically. Thank you for using our service.")

                    # Auto-logout after 3 seconds (user won't be able to login anyway)
                    import time
                    time.sleep(3)
                    for key in ['authenticated', 'user_uid', 'user_email', 'user_token']:
                        if key in st.session_state:
                            del st.session_state[key]
                    st.rerun()

                else:
                    st.error(f"❌ Failed to delete your account: {result['error']}")
                    st.info("Please try again or contact support if the problem persists.")


def sidebar_navigation():
    """Create sidebar navigation"""
    with st.sidebar:
        st.markdown("# 🏆 Quizdom")
        st.markdown("### Navigation")

        if st.button("🏠 Home", use_container_width=True):
            st.session_state.page = "home"
            # Clear auth session when going to home
            for key in ['authenticated', 'user_uid', 'user_email', 'user_token']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

        if st.button("🗑️ Delete Account", use_container_width=True):
            st.session_state.page = "deletion"
            st.rerun()

        st.markdown("---")
        st.markdown("*© 2025 Quizdom*")


def main():
    # Initialize Firebase
    try:
        initialize_firebase()
    except Exception as e:
        st.error(f"Failed to initialize Firebase: {e}")
        st.stop()

    # Initialize page state
    if 'page' not in st.session_state:
        st.session_state.page = "home"

    # Display sidebar navigation
    sidebar_navigation()

    # Route to appropriate page
    if st.session_state.page == "home":
        home_page()
    elif st.session_state.page == "deletion":
        # Check if user is authenticated for deletion page
        if not st.session_state.get('authenticated', False):
            login_page()
        else:
            deletion_page()


if __name__ == "__main__":
    main()