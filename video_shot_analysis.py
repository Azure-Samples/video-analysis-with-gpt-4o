# Import libraries
import streamlit as st
import cv2
import os
import time
import json
from dotenv import load_dotenv
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from moviepy.editor import VideoFileClip
from openai import AzureOpenAI
import base64
import yt_dlp
from yt_dlp.utils import download_range_func

# Default configuration
DEFAULT_SHOT_INTERVAL = 30  # In seconds
DEFAULT_FRAMES_PER_SECOND = 1
SYSTEM_PROMPT = "You are a helpful assistant that describes in detail a video. Respond in the same language as the transcription."
USER_PROMPT = "These are the frames from the video."
DEFAULT_TEMPERATURE = 0.5
RESIZE_OF_FRAMES = 4  # Changed default resize ratio to 4

# Load configuration
load_dotenv(override=True)

# Configuration of OpenAI GPT-4o
aoai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
aoai_apikey = os.environ["AZURE_OPENAI_API_KEY"]
aoai_apiversion = os.environ["AZURE_OPENAI_API_VERSION"]
aoai_model_name = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
system_prompt = os.environ.get("SYSTEM_PROMPT", "You are an expert on Video Analysis. You will be shown a series of images from a video. Describe what is happening in the video, including the objects, actions, and any other relevant details. Be as specific and detailed as possible.")
# print(f'aoai_endpoint: {aoai_endpoint}, aoai_model_name: {aoai_model_name}')
# Create AOAI client for answer generation
aoai_client = AzureOpenAI(
    azure_deployment=aoai_model_name,
    api_version=aoai_apiversion,
    azure_endpoint=aoai_endpoint,
    api_key=aoai_apikey
)

# Configuration of Whisper
whisper_endpoint = os.environ["WHISPER_ENDPOINT"]
whisper_apikey = os.environ["WHISPER_API_KEY"]
whisper_apiversion = os.environ["WHISPER_API_VERSION"]
whisper_model_name = os.environ["WHISPER_DEPLOYMENT_NAME"]
# Create AOAI client for whisper
whisper_client = AzureOpenAI(
    api_version=whisper_apiversion,
    azure_endpoint=whisper_endpoint,
    api_key=whisper_apikey
)

# Function to encode a local video into frames
def process_video(video_path, frames_per_second=DEFAULT_FRAMES_PER_SECOND, resize=RESIZE_OF_FRAMES, output_dir='', temperature=DEFAULT_TEMPERATURE):
    print(f"Starting video processing for {video_path} with frames_per_second={frames_per_second}, resize={resize}")
    base64Frames = []

    # Prepare the video analysis
    video = cv2.VideoCapture(video_path)
    total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = video.get(cv2.CAP_PROP_FPS)
    frames_to_skip = int(fps / frames_per_second)
    curr_frame = 0

    # Prepare to write the frames to disk
    if output_dir != '':
        os.makedirs(output_dir, exist_ok=True)
        frame_count = 1

    # Loop through the video and extract frames at the specified sampling rate
    while curr_frame < total_frames - 1:
        video.set(cv2.CAP_PROP_POS_FRAMES, curr_frame)
        success, frame = video.read()
        if not success:
            break

        print(f"Processing frame {curr_frame}/{total_frames}")

        # Resize the frame if required
        if resize != 0:
            height, width, _ = frame.shape
            frame = cv2.resize(frame, (width // resize, height // resize))

        _, buffer = cv2.imencode(".jpg", frame)

        # Save frame as JPG file if output_dir is specified
        if output_dir != '':
            frame_filename = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(video_path))[0]}_frame_{frame_count}.jpg")
            with open(frame_filename, "wb") as f:
                f.write(buffer)
            frame_count += 1

        base64Frames.append(base64.b64encode(buffer).decode("utf-8"))
        curr_frame += frames_to_skip
    video.release()
    print(f"Extracted {len(base64Frames)} frames from {video_path}")

    return base64Frames

# Function to transcript the audio from the local video with Whisper
def process_audio(video_path):
    print(f"Starting audio transcription for {video_path}")
    transcription_text = ''
    try:
        base_video_path, _ = os.path.splitext(video_path)
        audio_path = f"{base_video_path}.mp3"
        clip = VideoFileClip(video_path)
        clip.audio.write_audiofile(audio_path, bitrate="32k")
        clip.audio.close()
        clip.close()
        print(f"Extracted audio to {audio_path}")

        # Transcribe the audio
        print(f"Transcribing audio from {audio_path}")
        transcription = whisper_client.audio.transcriptions.create(
            model=whisper_model_name,
            file=open(audio_path, "rb"),
        )
        transcription_text = transcription.text
        print("Transcript: ", transcription_text + "\n\n")
    except Exception as ex:
        print(f'ERROR: {ex}')
        transcription_text = ''

    return transcription_text

# Function to analyze the video with GPT-4o
def analyze_video(base64frames, system_prompt, user_prompt, transcription, temperature):
    print(f"Starting video analysis with system_prompt={system_prompt} and user_prompt={user_prompt}")
    print(f"Number of frames to analyze: {len(base64frames)}")
    if transcription:
        print(f"Including audio transcription in the analysis")

    try:
        if transcription: # Include the audio transcription
            response = aoai_client.chat.completions.create(
                model=aoai_model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "user", "content": [
                        *map(lambda x: {"type": "image_url", "image_url": {"url": f'data:image/jpg;base64,{x}', "detail": "auto"}}, base64frames),
                        {"type": "text", "text": f"The audio transcription is: {transcription if isinstance(transcription, str) else transcription.text}"}
                    ]}
                ],
                temperature=temperature,
                max_tokens=4096
            )
        else: # Without the audio transcription
            response = aoai_client.chat.completions.create(
                model=aoai_model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "user", "content": [
                        *map(lambda x: {"type": "image_url", "image_url": {"url": f'data:image/jpg;base64,{x}', "detail": "auto"}}, base64frames),
                    ]}
                ],
                temperature=0.5,
                max_tokens=4096
            )

        json_response = json.loads(response.model_dump_json())
        response = json_response['choices'][0]['message']['content']
        print("Analysis completed successfully")

    except Exception as ex:
        print(f'ERROR: {ex}')
        response = f'ERROR: {ex}'

    return response

# Split the video into shots of N seconds
def split_video(video_path, output_dir, shot_interval=DEFAULT_SHOT_INTERVAL, max_duration=None):
    print(f"Starting video splitting for {video_path} with shot_interval={shot_interval}, max_duration={max_duration}")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    if max_duration is not None and max_duration > 0:
        duration = min(duration, max_duration)

    for start_time in range(0, int(duration), shot_interval):
        end_time = min(start_time + shot_interval, duration)
        output_file = os.path.join(output_dir, f'{os.path.splitext(os.path.basename(video_path))[0]}_shot_{start_time}-{end_time}_secs.mp4')
        print(f"Extracting shot from {start_time} to {end_time} into {output_file}")
        ffmpeg_extract_subclip(video_path, start_time, end_time, targetname=output_file)
        yield output_file

# Process the video
def execute_video_processing(st, shot_path, system_prompt, user_prompt, temperature, frames_per_second, analysis_dir):
    print(f"Starting video processing for shot {shot_path}")
    # Show the video on the screen
    st.write(f"Video: {shot_path}:")
    st.video(shot_path)

    with st.spinner(f"Analyzing video shot: {shot_path}"):
        # Extract frames at the specified rate
        with st.spinner(f"Extracting frames..."):
            start_time = time.time()
            if save_frames:
                output_dir = os.path.join(analysis_dir, 'frames')
            else:
                output_dir = ''
            print(f"Extracting frames from {shot_path}")
            base64frames = process_video(shot_path, frames_per_second=frames_per_second, resize=resize, output_dir=output_dir, temperature=temperature)
            end_time = time.time()
            print(f'\t>>>> Frames extraction took {(end_time - start_time):.3f} seconds <<<<')

        # Extract the transcription of the audio
        if audio_transcription:
            print(f"Transcribing audio from {shot_path}")
            msg = f'Analyzing frames and audio with {aoai_model_name}...'
            with st.spinner(f"Transcribing audio from video file..."):
                start_time = time.time()
                transcription = process_audio(shot_path)
                end_time = time.time()
            print(f'Transcription: [{transcription}]')
            if show_transcription:
                st.markdown(f"**Transcription**: {transcription}", unsafe_allow_html=True)
            print(f'\t>>>> Audio transcription took {(end_time - start_time):.3f} seconds <<<<')
        else:
            print(f"Skipping audio transcription")
            msg = f'Analyzing frames with {aoai_model_name}...'
            transcription = ''
        # Analyze the video frames and the audio transcription with GPT-4o
        with st.spinner(msg):
            print(f"Analyzing frames with {aoai_model_name}")
            start_time = time.time()
            analysis = analyze_video(base64frames, system_prompt, user_prompt, transcription, temperature)
            end_time = time.time()
        print(f'\t>>>> Analysis with {aoai_model_name} took {(end_time - start_time):.3f} seconds <<<<')

    st.success("Analysis completed.")
    print(f"Analysis completed for shot {shot_path}")
    
    # Print the analysis content
    print(f"Analysis content: {analysis}")
    
    # Save the analysis to a JSON file in the analysis directory
    analysis_filename = os.path.join(analysis_dir, os.path.splitext(os.path.basename(shot_path))[0] + "_analysis.json")
    with open(analysis_filename, 'w') as json_file:
        json.dump({"analysis": analysis}, json_file, indent=4)
    print(f"Analysis saved as: {analysis_filename}")

    return analysis

# Streamlit User Interface
st.set_page_config(
    page_title="Video Shot Analysis with GPT-4o",
    layout="centered",
    initial_sidebar_state="auto",
)
st.image("microsoft.png", width=100)
st.title('Video Shot Analysis with GPT-4o')

with st.sidebar:
    file_or_url = st.selectbox("Video source:", ["File", "URL"], index=0, help="Select the source, file or url")
    initial_split = 0

    if file_or_url == "URL":
        continuous_transmission = st.checkbox('Continuous transmission', False, help="Video of a continuous transmission")
        if continuous_transmission:
            initial_split = DEFAULT_SHOT_INTERVAL
        
    audio_transcription = st.checkbox('Transcribe audio', True, help="Extract the audio transcription and use in the analysis or not")
    if audio_transcription:
        show_transcription = st.checkbox('Show audio transcription', True, help="Present the audio transcription or not")
    shot_interval = st.number_input(label='Shot interval in seconds', min_value=0, value=DEFAULT_SHOT_INTERVAL, help="The video will be processed in shots based on the number of seconds specified in this field.")
    frames_per_second = st.number_input('Frames per second', DEFAULT_FRAMES_PER_SECOND, help="The number of frames to extract per second.")
    resize = st.number_input("Frames resizing ratio", min_value=0, value=RESIZE_OF_FRAMES, help="The size of the images will be reduced in proportion to this number while maintaining the height/width ratio. This reduction is useful for improving latency and reducing token consumption (0 to not resize)")
    save_frames = st.checkbox('Save the frames to the folder "frames"', True)
    temperature = float(st.number_input('Temperature for the model', DEFAULT_TEMPERATURE))
    system_prompt = st.text_area('System Prompt', system_prompt)
    user_prompt = st.text_area('User Prompt', USER_PROMPT)
    max_duration = st.number_input('Maximum duration to process (seconds)', 0, help="Specify the maximum duration of the video to process. If the video is longer, only this duration will be processed. Set to 0 to process the entire video.")

# Video file or Video URL
if file_or_url == 'File':
    video_file = st.file_uploader("Upload a video file", type=["mp4", "avi", "mov"])
else:
    url = st.text_area("Enter the URL:", value='https://www.youtube.com/watch?v=Y6kHpAeIr4c', height=10)

# Analyze the video when the button is pressed
if st.button("Analyze video", use_container_width=True, type='primary'):

    # Show parameters:
    print(f"PARAMETERS:")
    print(f"file_or_url: {file_or_url}, audio_transcription: {audio_transcription}, shot interval: {shot_interval}, frames per second: {frames_per_second}")
    print(f"resize ratio: {resize}, save_frames: {save_frames}, temperature: {temperature}, max_duration: {max_duration}")

    if file_or_url == 'URL': # Process Youtube video
        st.write(f'Analyzing video from URL {url}...')
        
        ydl_opts = {
                'format': '(bestvideo[vcodec^=av01]/bestvideo[vcodec^=vp9]/bestvideo)+bestaudio/best',
                'outtmpl': 'full_video.%(ext)s',
                'force_keyframes_at_cuts': True
        }
        ydl = yt_dlp.YoutubeDL(ydl_opts)
        info_dict = ydl.extract_info(url, download=False)
        video_title = info_dict.get('title', 'video')
        video_duration = int(info_dict.get('duration', 0))  # Convert to int

        # Create a directory for the video analysis
        analysis_dir = f"{video_title}_video_analysis"
        os.makedirs(analysis_dir, exist_ok=True)

        # Create subdirectories for shots and analysis
        shots_dir = os.path.join(analysis_dir, "shots")
        os.makedirs(shots_dir, exist_ok=True)
        analysis_subdir = os.path.join(analysis_dir, "analysis")
        os.makedirs(analysis_subdir, exist_ok=True)

        # Download the video if it doesn't already exist
        video_path = os.path.join(analysis_dir, f"{video_title}.mp4")
        if not os.path.exists(video_path):
            with st.spinner(f"Downloading video..."):
                ydl_opts['outtmpl'] = video_path
                ydl.download([url])
                print(f"Downloaded video: {video_path}")

        if max_duration > 0:
            video_duration = min(video_duration, max_duration)
        else:
            video_duration = int(info_dict.get('duration', 0))  # Convert to int

        if shot_interval == 0:
            segment_duration = video_duration
        else:
            segment_duration = int(shot_interval)  # Convert to int

        for start in range(0, video_duration, segment_duration):
            end = start + segment_duration
            shot_filename = f'shot_{start}-{end}.mp4'
            shot_path = os.path.join(shots_dir, shot_filename)
            with st.spinner(f"Extracting shot from second {start} to {end}..."):
                ffmpeg_extract_subclip(video_path, start, end, targetname=shot_path)
                print(f"Extracted shot: {shot_path}")

            # Process the video shot
            analysis = execute_video_processing(st, shot_path, system_prompt, user_prompt, temperature, frames_per_second, analysis_subdir)
            st.markdown(f"**Description**: {analysis}", unsafe_allow_html=True)

            # Example detecting an event
            event="electric guitar"
            if event in analysis:
                st.write(f'**Detected event "{event}" in shot {shot_path}**')

    else: # Process the video file
        if video_file is not None:
            video_title = os.path.splitext(video_file.name)[0]
            analysis_dir = f"{video_title}_video_analysis"
            os.makedirs(analysis_dir, exist_ok=True)

            # Create subdirectories for shots and analysis
            shots_dir = os.path.join(analysis_dir, "shots")
            os.makedirs(shots_dir, exist_ok=True)
            analysis_subdir = os.path.join(analysis_dir, "analysis")
            os.makedirs(analysis_subdir, exist_ok=True)

            video_path = os.path.join(analysis_dir, video_file.name)
            try:
                with open(video_path, "wb") as f:
                    f.write(video_file.getbuffer())
                print(f"Uploaded video file: {video_path}")

                # Splitting video into shots
                for shot_path in split_video(video_path, shots_dir, shot_interval, max_duration):
                    print(f"Processing shot: {shot_path}")
                    # Process the video shot
                    analysis = execute_video_processing(st, shot_path, system_prompt, user_prompt, temperature, frames_per_second, analysis_subdir)
                    st.markdown(f"**Description**: {analysis}", unsafe_allow_html=True)

            except Exception as ex:
                print(f'ERROR: {ex}')
                st.write(f'ERROR: {ex}')
