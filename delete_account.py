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


def check_account_exists(email):
    """Check if an account exists for this email."""
    try:
        user = auth.get_user_by_email(email)
        return {"exists": True, "uid": user.uid}
    except auth.UserNotFoundError:
        return {"exists": False}
    except Exception as e:
        return {"exists": False, "error": str(e)}


def send_sign_in_link(email):
    """Send a Firebase email sign-in link to the user."""
    try:
        api_key = st.secrets["FIREBASE_WEB_API_KEY"]
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
        payload = {
            "requestType": "EMAIL_SIGNIN",
            "email": email,
            "continueUrl": f"https://quizdom-account-deletion.streamlit.app/?email={requests.utils.quote(email)}",
            "canHandleCodeInApp": True,
        }
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            return {"success": True}
        error = response.json().get("error", {}).get("message", "Failed to send email")
        return {"success": False, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}


def verify_sign_in_link(email, oob_code):
    """Exchange the oobCode from the email link for a Firebase ID token."""
    try:
        api_key = st.secrets["FIREBASE_WEB_API_KEY"]
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithEmailLink?key={api_key}"
        payload = {"email": email, "oobCode": oob_code}
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "uid": data["localId"], "email": data["email"]}
        error = response.json().get("error", {}).get("message", "Verification failed")
        return {"success": False, "error": error}
    except Exception as e:
        return {"success": False, "error": str(e)}


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


# ---------------------------------------------------------------------------
# Firestore cleanup — mirrors UserCleanupManager + deleteUser() in the app
# ---------------------------------------------------------------------------

def _cleanup_users(db, user_id, log):
    """Delete the user's document from the users collection."""
    doc_ref = db.collection("users").document(user_id)
    if doc_ref.get().exists:
        doc_ref.delete()
        log.append("users/{userId} — deleted")
    else:
        log.append("users/{userId} — not found (skipped)")


def _cleanup_user_statistics(db, user_id, log):
    """Delete the user's statistics document."""
    doc_ref = db.collection("userStatistics").document(user_id)
    if doc_ref.get().exists:
        doc_ref.delete()
        log.append("userStatistics/{userId} — deleted")
    else:
        log.append("userStatistics/{userId} — not found (skipped)")


def _cleanup_friends(db, user_id, log):
    """
    Mirror of FriendsDataSource.clearUserFriendsData():
    - Remove user from all friends' friendsIds arrays
    - Remove user's sent friend requests from recipients' pendingRequestsReceived
    - Remove user's received friend requests from senders' pendingRequestsSent
    - Remove user's outgoing game requests from recipients' friendsGameRequestsReceived
    - Remove user's incoming game requests from senders' friendsGameRequestsSent
    - Remove user from blockedUsersIds of anyone who blocked them
    - Delete the user's own friends document
    """
    friends_ref = db.collection("friends")
    user_doc_snap = friends_ref.document(user_id).get()

    if not user_doc_snap.exists:
        log.append("friends/{userId} — not found (skipped)")
        return

    data = user_doc_snap.to_dict() or {}
    batch = db.batch()
    ops = 0

    # 1. Remove from friends' friendsIds arrays
    for friend_id in data.get("friendsIds", []):
        batch.update(friends_ref.document(friend_id), {
            "friendsIds": firestore.ArrayRemove([user_id])
        })
        ops += 1

    # 2. Remove user's sent requests from recipients' pendingRequestsReceived
    for recipient_id in data.get("pendingRequestsSent", {}).keys():
        batch.update(friends_ref.document(recipient_id), {
            f"pendingRequestsReceived.{user_id}": firestore.DELETE_FIELD
        })
        ops += 1

    # 3. Remove user's received requests from senders' pendingRequestsSent
    for sender_id in data.get("pendingRequestsReceived", {}).keys():
        batch.update(friends_ref.document(sender_id), {
            f"pendingRequestsSent.{user_id}": firestore.DELETE_FIELD
        })
        ops += 1

    # 4. Remove user's outgoing game requests from recipients' friendsGameRequestsReceived
    for recipient_id in data.get("friendsGameRequestsSent", {}).keys():
        batch.update(friends_ref.document(recipient_id), {
            f"friendsGameRequestsReceived.{user_id}": firestore.DELETE_FIELD
        })
        ops += 1

    # 5. Remove user's incoming game requests from senders' friendsGameRequestsSent
    for sender_id in data.get("friendsGameRequestsReceived", {}).keys():
        batch.update(friends_ref.document(sender_id), {
            f"friendsGameRequestsSent.{user_id}": firestore.DELETE_FIELD
        })
        ops += 1

    # 6. Remove user from blockedUsersIds of anyone who blocked them (requires a query)
    blocked_by_snaps = friends_ref.where("blockedUsersIds", "array_contains", user_id).get()
    for doc in blocked_by_snaps:
        batch.update(doc.reference, {
            "blockedUsersIds": firestore.ArrayRemove([user_id])
        })
        ops += 1

    # 7. Delete the user's own friends document
    batch.delete(friends_ref.document(user_id))
    ops += 1

    batch.commit()
    log.append(f"friends — cleaned up ({ops} operations)")


def _cleanup_general_trivia_rooms(db, user_id, log):
    """
    Mirror of GeneralTriviaRoomDataSource.removeUserFromAllRooms():
    Remove user from topUsers map in every generalTriviaRooms document.
    """
    rooms_ref = db.collection("generalTriviaRooms")
    snapshot = rooms_ref.get()
    batch = db.batch()
    changed = 0

    for doc in snapshot:
        data = doc.to_dict() or {}
        top_users = data.get("topUsers", {})
        if user_id in top_users:
            updated = {k: v for k, v in top_users.items() if k != user_id}
            batch.update(doc.reference, {"topUsers": updated})
            changed += 1

    batch.commit()
    log.append(f"generalTriviaRooms — removed from {changed} room(s)")


def _cleanup_monthly_leaderboard(db, user_id, log):
    """
    Mirror of MonthlyLeaderboardDataSource.removeUserFromLeaderboards():
    Filter user out of topScore and topQuestProgress arrays in every monthly doc.
    """
    lb_ref = db.collection("monthlyLeaderboards")
    snapshot = lb_ref.get()
    batch = db.batch()
    changed = 0

    for doc in snapshot:
        data = doc.to_dict() or {}
        top_score = data.get("topScore", [])
        top_quest = data.get("topQuestProgress", [])

        filtered_score = [e for e in top_score if e.get("userId") != user_id]
        filtered_quest = [e for e in top_quest if e.get("userId") != user_id]

        if len(filtered_score) != len(top_score) or len(filtered_quest) != len(top_quest):
            batch.update(doc.reference, {
                "topScore": filtered_score,
                "topQuestProgress": filtered_quest,
            })
            changed += 1

    batch.commit()
    log.append(f"monthlyLeaderboards — removed from {changed} month(s)")


def _cleanup_quest_beast(db, user_id, log):
    """
    Mirror of QuestBeastDataSource.cleanupUserData():
    Delete the user's document from the questBeast collection.
    """
    doc_ref = db.collection("questBeast").document(user_id)
    if doc_ref.get().exists:
        doc_ref.delete()
        log.append("questBeast/{userId} — deleted")
    else:
        log.append("questBeast/{userId} — not found (skipped)")


def _cleanup_available_players(db, user_id, log):
    """
    Mirror of UserPreferenceDataSource.cleanupUserPreference():
    - If the user was matched, remove them from the matched user's document
    - Delete the user's own availablePlayers document
    """
    players_ref = db.collection("availablePlayers")
    doc_snap = players_ref.document(user_id).get()

    if not doc_snap.exists:
        log.append("availablePlayers/{userId} — not found (skipped)")
        return

    data = doc_snap.to_dict() or {}
    matched_user_id = data.get("matchedUserId")

    if matched_user_id:
        # Remove this user from the matched user's document
        matched_snap = players_ref.document(matched_user_id).get()
        if matched_snap.exists:
            matched_data = matched_snap.to_dict() or {}
            # Only clear the match if it still points back to the deleted user
            if matched_data.get("matchedUserId") == user_id:
                players_ref.document(matched_user_id).update({
                    "matchedUserId": firestore.DELETE_FIELD,
                    "triviaRoomId": firestore.DELETE_FIELD,
                })

    players_ref.document(user_id).delete()
    log.append("availablePlayers/{userId} — deleted" + (f" (unmatched {matched_user_id})" if matched_user_id else ""))


def _cleanup_pin_verification(db, user_email, log):
    """
    Mirror of PinVerificationDataSource.cleanupPinVerification():
    Delete the PIN verification document keyed by email.
    """
    if not user_email:
        log.append("pinVerifications — no email available (skipped)")
        return

    doc_ref = db.collection("pinVerifications").document(user_email)
    if doc_ref.get().exists:
        doc_ref.delete()
        log.append(f"pinVerifications/{user_email} — deleted")
    else:
        log.append(f"pinVerifications/{user_email} — not found (skipped)")


def delete_all_user_data(db, user_id, user_email):
    """
    Complete Firestore cleanup matching the Flutter app's UserCleanupManager
    plus the PIN verification cleanup done in deleteUser().

    Order mirrors the app:
      1. UserDataSource          → users/{userId}
      2. UserStatisticsDataSource → userStatistics/{userId}
      3. FriendsDataSource        → friends collection (cascading)
      4. GeneralTriviaRoomDataSource → generalTriviaRooms (topUsers map)
      5. MonthlyLeaderboardDataSource → monthlyLeaderboards (arrays)
      6. QuestBeastDataSource     → questBeast/{userId}
      7. UserPreferenceDataSource → availablePlayers/{userId} + matched user
      8. PIN verification         → pinVerifications/{email}

    Returns (success: bool, log: list[str], error: str|None)
    """
    log = []
    try:
        _cleanup_users(db, user_id, log)
        _cleanup_user_statistics(db, user_id, log)
        _cleanup_friends(db, user_id, log)
        _cleanup_general_trivia_rooms(db, user_id, log)
        _cleanup_monthly_leaderboard(db, user_id, log)
        _cleanup_quest_beast(db, user_id, log)
        _cleanup_available_players(db, user_id, log)
        _cleanup_pin_verification(db, user_email, log)
        return True, log, None
    except Exception as e:
        return False, log, str(e)


def complete_user_deletion(db, user_id, user_email):
    """Complete user deletion: Firestore cleanup first, then Firebase Auth."""
    log = []

    # Step 1: Delete all Firestore data first (matches app behaviour — data
    # cleanup runs before firebaseUser.delete() so auth stays intact on failure)
    firestore_ok, firestore_log, firestore_err = delete_all_user_data(db, user_id, user_email)
    log.extend(firestore_log)

    if not firestore_ok:
        return {
            "success": False,
            "error": f"Firestore cleanup failed: {firestore_err}",
            "log": log,
        }

    # Step 2: Delete from Firebase Authentication
    auth_deleted = delete_user_from_auth(user_id)

    return {
        "success": auth_deleted,
        "auth_deleted": auth_deleted,
        "log": log,
        "error": None if auth_deleted else "Firebase Auth deletion failed",
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

        st.link_button(label="📱 Download on Google Play", type="primary", url="https://play.google.com/store/apps/details?id=com.yinonhdev.quizdom", use_container_width=True)

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
            <a href="mailto:yinon.h21+quizdom@gmail.com" style="color: #00AFFF; text-decoration: none;">Contact Support</a>
        </p>
    </div>
    """, unsafe_allow_html=True)


def login_page():
    """Display login page"""
    st.title("Quizdom Account Deletion Portal")

    # IMPORTANT NOTICE
    st.empty()
    st.error(
        "**🚨 Important:** You must be **logged out** of the main app before deleting your account from here.\n\n"
        "If you still have access to your account, it is **recommended** to delete your account through the **Profile** screen in the app."
    )
    st.markdown("---")

    # --- Step 2: user landed back after clicking the email link ---
    oob_code = st.query_params.get("oobCode")
    pending_email = st.session_state.get("pending_email") or st.query_params.get("email")

    if oob_code and pending_email:
        with st.spinner("Verifying link..."):
            result = verify_sign_in_link(pending_email, oob_code)
        if result["success"]:
            st.session_state.authenticated = True
            st.session_state.user_uid = result["uid"]
            st.session_state.user_email = result["email"]
            st.session_state.user_token = None
            st.session_state.pop("pending_email", None)
            st.query_params.clear()
            st.rerun()
        else:
            st.error(f"Verification failed: {result['error']}")
            st.session_state.pop("pending_email", None)
            st.query_params.clear()
        return

    # --- Step 1b: waiting for user to click the link ---
    if st.session_state.get("awaiting_email_link"):
        st.subheader("Check Your Email")
        st.success(f"A sign-in link was sent to **{pending_email}**. Click the link in the email to continue.")
        st.info("Once you click the link you'll be redirected back here automatically.")
        st.warning("Didn't receive the email? Check your **spam or junk folder**.")
        if st.button("Back"):
            st.session_state.pop("awaiting_email_link", None)
            st.session_state.pop("pending_email", None)
            st.rerun()
        return

    # --- Step 1: login form ---
    st.write("Please log in with your Quizdom account to request deletion.")

    with st.form("login_form"):
        st.subheader("Login to Your Account")
        email = st.text_input("Email Address")
        login_button = st.form_submit_button("Send Sign-in Link")

        if login_button:
            if not email:
                st.error("Please enter your email address.")
                return

            with st.spinner("Checking account..."):
                account_info = check_account_exists(email)

            if not account_info["exists"]:
                if "error" in account_info:
                    st.error(f"Error looking up account: {account_info['error']}")
                else:
                    st.error("No account found with that email address.")
                return

            with st.spinner("Sending sign-in link..."):
                result = send_sign_in_link(email)
            if result["success"]:
                st.session_state.awaiting_email_link = True
                st.session_state.pending_email = email
                st.rerun()
            else:
                st.error(f"Failed to send email: {result['error']}")

    # Information section
    st.markdown("---")
    st.subheader("ℹ️ Account Deletion Information")
    st.markdown("""
    **What happens when you delete your account:**
    - Your user profile and account information will be permanently deleted
    - All game statistics and history will be removed
    - Your entries will be removed from all leaderboards
    - Your friend connections and game requests will be cleaned up
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

    st.markdown("---")

    # Deletion confirmation section
    st.subheader("⚠️ Permanent Account Deletion")
    st.error("This action cannot be undone. Your account and all associated data will be permanently deleted.")

    # Confirmation checkboxes
    understand_permanent = st.checkbox("I understand this deletion is permanent and irreversible")
    understand_data_loss = st.checkbox("I understand all my game data, statistics, and progress will be lost")
    understand_no_recovery = st.checkbox("I understand my account cannot be recovered after deletion")

    if understand_permanent and understand_data_loss and understand_no_recovery:
        delete_button = st.button(
            "🗑️ DELETE MY ACCOUNT PERMANENTLY",
            type="primary",
        )

        if delete_button:
            with st.spinner("Deleting your account... Please wait."):
                result = complete_user_deletion(
                    db,
                    st.session_state.user_uid,
                    st.session_state.user_email,
                )

                if result["success"]:
                    st.success("✅ Your account has been successfully deleted!")

                    st.subheader("Deletion Summary:")
                    st.write(f"**Authentication:** {'✅ Deleted' if result['auth_deleted'] else '❌ Failed'}")
                    st.write("**Firestore cleanup:**")
                    for entry in result["log"]:
                        st.write(f"  - {entry}")

                    st.balloons()
                    st.info("You will be logged out automatically. Thank you for using our service.")

                    import time
                    time.sleep(3)
                    for key in ['authenticated', 'user_uid', 'user_email', 'user_token']:
                        if key in st.session_state:
                            del st.session_state[key]
                    st.rerun()

                else:
                    st.error(f"❌ Failed to delete your account: {result['error']}")
                    if result.get("log"):
                        with st.expander("Partial deletion log"):
                            for entry in result["log"]:
                                st.write(f"  - {entry}")
                    st.info("Please try again or contact support if the problem persists.")


def sidebar_navigation():
    """Create sidebar navigation"""
    with st.sidebar:
        st.markdown("# 🏆 Quizdom")

        if st.button("🏠 Home", use_container_width=True):
            st.session_state.page = "home"
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

    if 'page' not in st.session_state:
        st.session_state.page = "home"

    sidebar_navigation()

    if st.session_state.page == "home":
        home_page()
    elif st.session_state.page == "deletion":
        if not st.session_state.get('authenticated', False):
            login_page()
        else:
            deletion_page()


if __name__ == "__main__":
    main()
