import streamlit as st
import openai
import time
from io import BytesIO
from pdfminer.high_level import extract_text
from typing import Optional
import requests
from urllib.parse import quote
from bs4 import BeautifulSoup
import re

MAX_RETRIES = 3
RETRY_DELAY = 2
MAX_RESUME_SIZE = 5 * 1024 * 1024
MAX_JOB_DESCRIPTION_LENGTH = 50000
MAX_QUESTIONS_PER_SESSION = 500  # New constant for question limit

# WebScrapingAPI credentials
API_KEY = st.secrets["WEBSCRAPING_API_KEY"]
SCRAPER_URL = 'https://api.webscrapingapi.com/v2'

class SessionState:
    def __init__(self):
        self.start_chat = False
        self.thread_id: Optional[str] = None
        self.buttons_shown = False
        self.resume_text = ""
        self.job_description_text = ""
        self.job_type = ""
        self.messages = []
        self.processing = False
        self.job_input_method = "url"
        self.job_url = ""
        self.resume_input_method = "paste"
        self.assistant_is_typing = False
        self.question_count = 0  # New field to track number of questions asked

def get_session_state():
    if 'session_state' not in st.session_state:
        st.session_state.session_state = SessionState()
    return st.session_state.session_state

class APIError(Exception):
    pass

def safe_api_call(func, *args, **kwargs):
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except openai.error.OpenAIError as e:
            if attempt == MAX_RETRIES - 1:
                raise APIError(f"API call failed after {MAX_RETRIES} attempts: {str(e)}")
            time.sleep(RETRY_DELAY)

def validate_resume(uploaded_file):
    if uploaded_file is None:
        return False, "No file uploaded"
    if uploaded_file.size > MAX_RESUME_SIZE:
        return False, f"File size exceeds {MAX_RESUME_SIZE // (1024 * 1024)} MB limit"
    if uploaded_file.type != "application/pdf":
        return False, "Only PDF files are accepted"
    return True, ""

def validate_job_description(text):
    if not text:
        return False, "Job description is empty"
    if len(text) > MAX_JOB_DESCRIPTION_LENGTH:
        return False, f"Job description exceeds {MAX_JOB_DESCRIPTION_LENGTH} characters"
    return True, ""

def extract_pdf_content(uploaded_file):
    try:
        pdf_stream = BytesIO(uploaded_file.read())
        text = extract_text(pdf_stream)
        return text
    except Exception as e:
        raise ValueError(f"Error extracting PDF content: {str(e)}")

def create_thread():
    return safe_api_call(openai.beta.threads.create)

def create_message(thread_id, role, content):
    return safe_api_call(openai.beta.threads.messages.create,
                         thread_id=thread_id,
                         role=role,
                         content=content)

def create_run(thread_id, assistant_id):
    return safe_api_call(openai.beta.threads.runs.create,
                         thread_id=thread_id,
                         assistant_id=assistant_id)

def get_run_status(thread_id, run_id):
    return safe_api_call(openai.beta.threads.runs.retrieve,
                         thread_id=thread_id,
                         run_id=run_id)

def get_messages(thread_id):
    return safe_api_call(openai.beta.threads.messages.list,
                         thread_id=thread_id)

def fetch_content(target_url):
    encoded_url = quote(target_url)
    params = {
        "api_key": API_KEY,
        "url": encoded_url,
    }
    response = requests.get(SCRAPER_URL, params=params)
    if response.status_code == 200:
        return response.text
    else:
        return None

def extract_and_clean_text(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    raw_text = soup.get_text(separator='\n')
    cleaned_text = re.sub(r'\n+', '\n', raw_text)
    cleaned_text = re.sub(r'[ \t]+', ' ', cleaned_text)
    cleaned_text = '\n'.join(line.strip() for line in cleaned_text.splitlines() if line.strip())
    return cleaned_text.strip()

def main():
    st.set_page_config(page_title="JSON AI- Job Search on the Got", page_icon=":briefcase:")
    state = get_session_state()

    openai.api_key = st.secrets["OPENAI_API_KEY"]
    assistant_id = st.secrets["ASSISTANT_KEY_PERSONAL"]

    st.title("JSON AI- Job Search on the Go!")
    st.write("I am JSON, here to help you craft compelling messages for your job applications, from resumes to cover letters and LinkedIn connections.")

    if state.question_count >= MAX_QUESTIONS_PER_SESSION:
        st.error(f"You have reached the maximum number of questions ({MAX_QUESTIONS_PER_SESSION}) for this session. This assistant is currently in beta, there is a limit of questions per session. Please start a new session.")
        if st.button("Start New Session"):
            reset_chat(state)
            st.rerun()
        return

    st.sidebar.title("Get Personalized Job Application Assistance")
    
    state.job_type = st.sidebar.selectbox(
        "What type of job are you looking for? :red[*]",
        ["Product Management", "Product Marketing", "Project Management", "Software Engineering", "Strategic Account Management"],
        index=0
    )

    state.resume_input_method = st.sidebar.radio(
        "Share more context:", 
        ("Upload Context", "Paste your context"), 
        index=1,
        help="Upload your context in PDF format or manually type your context content. This helps our AI understand your qualifications and experience."
    )

    if state.resume_input_method == "Upload Context":
        uploaded_resume = st.sidebar.file_uploader("Upload your Context (PDF)", type="pdf", label_visibility="collapsed")
        if uploaded_resume:
            is_valid, message = validate_resume(uploaded_resume)
            if is_valid:
                state.resume_text = extract_pdf_content(uploaded_resume)
            else:
                st.sidebar.error(message)
    else:
        state.resume_text = st.sidebar.text_area("Share more about yourself:", placeholder="I am a PM with 5+ years of experience in B2B SaaS and FinTech. I have worked across.....", height=200)
    
    state.job_input_method = st.sidebar.radio(
        "Choose job input method: :red[*]", 
        ("Enter Job URL", "Enter Job Description"),
        help="Provide a link to the job posting or manually paste the job posting. AI Assistant will extract the job description to tailor your messages. Note that some sites have strict guidelines that may prevent us from accessing the information. If this occurs, manually paste the job description."
    )

    if state.job_input_method == "Enter Job Description":
        state.job_description_text = st.sidebar.text_area("Paste Job Details here: :red[*]", placeholder="The Associate Product Manager (APM) program is focused on building Google's next generation of product leaders.....", height=200)
    else:
        state.job_url = st.sidebar.text_input("Enter Job URL :red[*]")

    if st.sidebar.button("Start Chat"):
        if not state.job_type or (state.job_input_method == "Enter Job Description" and not state.job_description_text) or (state.job_input_method == "Enter Job URL" and not state.job_url):
            st.sidebar.error("Please fill in all required fields marked with :red[*]")
        elif state.job_input_method == "Enter Job Description":
            is_valid, message = validate_job_description(state.job_description_text)
            if is_valid:
                state.processing = True
                st.rerun()
            else:
                st.sidebar.error(message)
        else:
            if state.job_url:
                with st.spinner("Fetching job description from URL..."):
                    html_content = fetch_content(state.job_url)
                    if html_content:
                        state.job_description_text = extract_and_clean_text(html_content)
                        st.success("Job description successfully fetched from URL.")
                        state.processing = True
                        st.rerun()
                    else:
                        st.error("Failed to retrieve content. Please check the URL and try again.")
                        time.sleep(2)
            else:
                st.sidebar.error("Please enter a valid Job URL.")

    if state.processing:
        with st.spinner("Initializing chat..."):
            start_chat(state, assistant_id)
            state.processing = False
            st.rerun()

    if st.button("Exit Chat"):
        reset_chat(state)
        st.rerun()

    if state.start_chat:
        chat_interface(state, assistant_id)
    else:
        st.write("Provide your resume and job details, and JSON AI will analyze the information to offer tailored advice and templates that highlight your strengths and align with the job requirements.")

    st.sidebar.caption(f"Remaining credits for this session: {MAX_QUESTIONS_PER_SESSION - state.question_count}")
     # Divider and additional content at the bottom of the sidebar
    st.sidebar.markdown("---")
    st.sidebar.caption(
        'Made with ❤️ by [Utkarsh Khandelwal](https://www.linkedin.com/in/utkarshk1/)',
        unsafe_allow_html=True
    )

def start_chat(state, assistant_id):
    # Reset all relevant state variables
    state.start_chat = True
    state.buttons_shown = False
    state.messages = []  # Clear previous messages
    state.processing = False
    state.assistant_is_typing = False

# Reset the previous question selection for predefined questions
    st.session_state.previous_question = "Select a predefined question"

    # Create a new thread for each chat session
    thread = create_thread()
    state.thread_id = thread.id

    send_initial_messages(state, assistant_id)

def send_initial_messages(state, assistant_id):
    initial_prompt = f"""
    Utkarsh is applying for a {state.job_type} role. Please review and analyze the provided job description, highlighting the following points in bullets: Company Name, Role, Desired Years of Experience, Expectations from the company (desired skill set for the role in 2-3 short points), and Alignment (how Utkarsh's skills, projects, and experiences align with the job requirements in 2-3 short bullet points). If the job description is irrelevant or incomplete, kindly request a resubmission. Keep your response within 125 words. Job Info: {state.job_description_text}, and Here are Utkarsh optional notes: {state.resume_text}
    """
    create_message(state.thread_id, "user", initial_prompt)
    run_assistant(state, assistant_id)

    second_prompt = f"""
    Based on Utkarsh’s profile and the provided job description, create seven distinct LinkedIn connection request messages for different target audiences in the job funnel- Hiring Manager, Recruiter, Alumnus, and Cold Network. Each message should be under 350 characters, convey enthusiasm for the opportunity, highlight Utkarsh's relevant experiences and projects that align with the job description. If possible, include metrics and use abbreviations. Use the following templates for reference:
    HM Message 1: ‘Hi [HM Name],\n I'd love to discuss a [Role-short form] within your team at [Company Name]. My [relevant experience] aligns well with [specific skills needed by team].’
    Recruiter Message 1: ‘Hi [Recruiter],(/n)I’m interested in the [Role-short form] position at [Company name]. My [relevant background] and [x]+ yrs of [relevant experience] align well with the team's needs. I'd appreciate a chance to chat!’
    Alumnus Message: ‘Hi Alumnus,/n I’m a fellow [School Short form] alum (20XX). I’m interested in a [Role- short form] role at [Company], and my [relevant experience] aligns closely with the [skills team is seeking]. I’d appreciate any insights or a referral if possible!’
    Cold Message 1: ‘I admire your team at [Company] for [relevant work team is doing]. My [relevant experience] aligns with a [opportunity in your team], and I'd appreciate a chance to chat!’
    Cold Message 2: 'I've always admired [Company] for its [relevant work]. I’m interested in a [Role- Short form] role in [Specific Team/Product]. My 4+ yrs of PM experience [relevant experience] align well with the team's needs. I'd appreciate a chance to chat!'
    Cold Message 3: 'Hi (Their Name),I know you’re busy. I just wanted to say, for X role you have open: \n You’re looking for (X years of experience) in PM: I have (Y years of experience) \n You’re looking for (Z functional experience): I did that at (A company) \n Your company is going after (B mission): I dig that \n (Insert proof of work). Let’s chat?',
    Cold Message 4: 'Hey (HM Name), I believe my (X experience) qualifies me for your (Y role), and I love what (Z company is doing). Let’s connect?'
    """
    create_message(state.thread_id, "user", second_prompt)

    run_assistant(state, assistant_id)

def chat_interface(state, assistant_id):
    messages_container = st.container()
    
    with messages_container:
        for message in state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    predefined_questions = {
        "Select a predefined question": "",  # Default option
    "LinkedIn Request | Hiring Manager (300 characters)": 
        "Based on {candidate name}'s resume and the provided job description, create four distinct LinkedIn connection request messages for the hiring manager of the job. Each message should be under 300 characters, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications. If possible, include metrics and use abbreviations. Use a different combination of templates and experiences for each example: ‘Hi [HM Name],(/n)I'm interested in the [Role- short form] at [Company Name]. I have [highlight relevant experience and background], which I believe will make me a great fit for your team.’ Option 2: ‘ Hi [HM Name],(/n)I’m interested in the [Role- short form] in your team. My x+ yrs of PM experience- [relevant experience] aligns well with the position, and could [contribute to product’s success].’ Option 3: ‘Hi [HM Name],\n I'd love to discuss a [Role-short form] within your team at [Company Name]. My [relevant experience] aligns well with [specific skills needed by team].’ ",
    "LinkedIn Request | Recruiter (300 characters)": 
        " Based on the candidate's resume and the provided job description, create four distinct LinkedIn connection request messages for the recruiter of the job. Each message should be under 300 characters, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications. If possible, include metrics and use abbreviations. Use a different combination of templates and experiences for each example: ‘Hi [Recruiter],(/n)I’m interested in the [Role-short form] position at [Company name]. My [relevant background] and [x]+ yrs of [relevant experience] align well with the team's needs. I'd love to discuss this further!’ Option2: ‘Hi [Recruiter],(/n)I'm interested in a [Role- short form] role at [Company name] and believe my [relevant background] is a strong fit. My [x]+ yrs [relevant experience] align perfectly with the team's needs. I'd love to discuss this further!’ Option 3: ‘Hi [Recruiter],(/n)I'm interested in a [Role- short form] at [Company name]. My [relevant background] and [x] yrs of PM experience in [relevant experience] aligns well with the position. I believe I will be a great fit to the team!'",
    "LinkedIn Request | Alumni Referral (300 characters)": 
        "Based on the candidate's resume and the provided job description, create four distinct LinkedIn connection request messages to an alumni for referral. Each message should be under 300 characters, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications. Use abbreviations when possible. Use a different combination of templates and experiences for each example: ‘Hi Alumnus,/n I’m a fellow [School Short form] alum (20XX). Would you be open to referring me for a [Role- short form] role at [Company]? My [relevant experience] aligns well with [relevant experience the team is looking for]. Grateful for your help!’ Option 2: ‘Hi Alumnus,/n I’m a fellow [School Short form] alum (20XX). I’m interested in a [Role- short form] role at [Company], and my [relevant experience] aligns closely with the [skills team is seeking]. I’d appreciate any insights or a referral if possible!’",
    "LinkedIn Request | Cold Network (300 characters)": 
        "Based on the candidate's resume and the provided job description, create four distinct LinkedIn connection request messages to cold network. Each message should be under 300 characters, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications. Use abbreviations when possible. Use a different combination of templates and experiences for each example: ‘I admire your team at [Company] for [relevant work team is doing]. My [relevant experience] aligns with a [opportunity in your team], and I'd appreciate a chance to chat!’ Option2: ‘I've always admired [Company] for its [relevant work]. I'm very interested in a [Role-Short form at Company], and my [relevant background] is a strong fit. I'd love to chat!’",
    "Linkedin InMail | Hiring Manager": 
        "Based on candidate resume and the provided job description, create two distinct LinkedIn messages for the hiring manager of the job. Each message should be under 200 words, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications that align with the job requirements. If possible, include metrics and use abbreviations. Use following templates as example: ‘Hi HM,/n I've long admired [Company] for [relevant work]. I'm very interested in the [Role] within your team, and my [relevant background] is a strong fit.\n My [x]+ years of [relevant experience] align well with the team’s needs:\n [three short bullet points with metrics if possible].\n I'd love to connect and discuss this opportunity further. Here is a link to my product portfolio: [link]’ Option2: ‘Hi [HM Name],\n I'd love to discuss a [Role-short form] within your team at [Company Name]. My [relevant background] and [x]+ years of [relevant experience] align closely with the role: :\n [three short bullet points with metrics if possible]. I'm passionate about [the target company sector] and admire [Company's innovating approach]. I’d love to discuss this opportunity further. Here is my product portfolio: [link]'",
    "Linkedin InMail | Recruiter": 
        "Based on candidate resume and the provided job description, create two distinct LinkedIn messages for the recruiter of the job. Each message should be under 200 words, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications that align with the job requirements. If possible, include metrics and use abbreviations. Use following templates as example: ‘Hi Recruiter,/n I've long admired [Company] for [relevant work]. I'm very interested in the [Role] at [Company Name], and my [relevant background] is a strong fit.\n My [x]+ years of [relevant experience] align well with the team’s needs:\n [three short bullet points with metrics if possible].\n I'd love to connect and discuss this opportunity further. Here is the job link: [link]’ Option2: ‘Hi [Recruiter],\n I've been following [Company Name] for a while now- big admirer of [Company relevant highlight]. I'm interested in a [Role- short form] role at [Company name] and believe my [relevant background] aligns well with the team's needs: \n [three short bullet points with metrics if possible].I'd appreciate the chance to discuss this further. My resume is attached for your review.  Here is the job link: [link]’",
    "Linkedin InMail | Alumni Referral": 
        "Based on candidate resume and the provided job description, create two distinct LinkedIn messages for cold network. Each message should be under 200 words, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications that align with the job requirements. If possible, include metrics and use abbreviations. Use different relevant experiences and use the following template as example : ‘Hi [Alumnus],\n I’m a [School Short form] alum (20XX)! \n I've been following [Company Name] for a while now- big admirer of [Company relevant highlight]. I'm interested in a [Role- short form] role at [Company name] and believe my [relevant background] aligns well with the team's needs: \n [two short bullet points with metrics if possible].\n  I'd be incredibly grateful if you can help me to connect to the hiring team or refer me to the position if possible. \n Here is my product portfolio: [link]  \nJob Link: [Link]’  \n Thanks in advance!’",
    "Linkedin InMail | Cold Referral": 
        "Based on candidate resume and the provided job description, create two distinct LinkedIn messages for cold network. Each message should be under 200 words, convey enthusiasm for the opportunity, and highlight relevant experience and qualifications that align with the job requirements. If possible, include metrics and use abbreviations. Use different relevant experiences and use the following template as example : ‘Hi [Name],\n I've been following [Company Name] for a while now- big admirer of [Company relevant highlight]. I'm interested in a [Role- short form] role at [Company name] and believe my [relevant background] aligns well with the team's needs: \n [two short bullet points with metrics if possible].\n If there's an opportunity to connect with the team and discuss my qualifications further, I'd be incredibly grateful! \n Here is my product portfolio: [link]  \nJob Link: [Link]’",
    "Cold Email | Hiring Manager": 
        "Based on the candidate resume and the provided job description, craft a cold email to the hiring manager. The email should be under 300 words, mention three relevant pointers highlighting the candidate's skills and experiences, and express interest in the role. Share that the resume and a portfolio have been attached for reference.",
    "Cold Email | Hiring Manager | Aakash ": 
        "Body: Hi (Their Name),I know you’re busy. I just wanted to say, for X role you have open: \n You’re looking for (X years of experience) in PM: I have (Y years of experience) \n You’re looking for (Z functional experience): I did that at (A company) \n Your company is going after (B mission): I dig that \n (Insert proof of work). Let’s chat?",
        }

    if user_input := st.chat_input("How can I assist you with your job interview messaging?"):
        if state.question_count < MAX_QUESTIONS_PER_SESSION:
            state.question_count += 1
            state.messages.append({"role": "user", "content": user_input})
            with messages_container.chat_message("user"):
                st.markdown(user_input)

            create_message(state.thread_id, "user", user_input)
            run_assistant(state, assistant_id)
            st.rerun()
        else:
            st.error(f"You have reached the maximum number of questions ({MAX_QUESTIONS_PER_SESSION}) for this session. Please start a new session.")
            reset_chat(state)
            st.rerun()

    if state.start_chat and not state.assistant_is_typing:
        if 'previous_question' not in st.session_state:
            st.session_state.previous_question = "Select a predefined question"
        
        selected_question = st.selectbox("Select a predefined question", 
                                         list(predefined_questions.keys()),
                                         key="predefined_question")
        
        if selected_question != "Select a predefined question" and selected_question != st.session_state.previous_question:
            if state.question_count < MAX_QUESTIONS_PER_SESSION:
                state.question_count += 1
                detailed_question = predefined_questions[selected_question]
                state.messages.append({"role": "user", "content": selected_question})
                with messages_container.chat_message("user"):
                    st.markdown(selected_question)

                create_message(state.thread_id, "user", detailed_question)
                run_assistant(state, assistant_id)
                
                st.session_state.previous_question = selected_question
                st.rerun()
            else:
                st.error(f"You have reached the maximum number of questions ({MAX_QUESTIONS_PER_SESSION}) for this session. Please start a new session.")
                reset_chat(state)
                st.rerun()

def run_assistant(state, assistant_id):
    run = create_run(state.thread_id, assistant_id)
    
    state.assistant_is_typing = True
    with st.spinner("Assistant is thinking..."):
        while run.status != 'completed':
            time.sleep(1)
            run = get_run_status(state.thread_id, run.id)
    
    messages = get_messages(state.thread_id)
    
    assistant_messages = [
        message for message in messages
        if message.run_id == run.id and message.role == "assistant"
    ]
    
    for message in assistant_messages:
        state.messages.append({"role": "assistant", "content": message.content[0].text.value})
    
    state.assistant_is_typing = False

def reset_chat(state):
    state.messages = []
    state.start_chat = False
    state.thread_id = None
    state.buttons_shown = False
    state.assistant_is_typing = False
    state.question_count = 0  # Reset question count
    state.resume_text = ""  # Reset resume text
    state.job_description_text = ""  # Reset job description text
    state.job_type = ""  # Reset job type
    state.job_url = ""  # Reset job URL


if __name__ == "__main__":
    main()
