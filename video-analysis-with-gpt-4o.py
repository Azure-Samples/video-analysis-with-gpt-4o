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
SEGMENT_DURATION = 20 # In seconds, Set to 0 to not split the video
SYSTEM_PROMPT = "You are a helpful assistant that describes in detail a video. Response in the same language than the transcription."
USER_PROMPT = "These are the frames from the video."
DEFAULT_TEMPERATURE = 0.5
RESIZE_OF_FRAMES = 2
SECONDS_PER_FRAME = 30

# Load configuration
load_dotenv(override=True)

# Configuration of OpenAI GPT-4o
aoai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
aoai_apikey = os.environ["AZURE_OPENAI_API_KEY"]
aoai_apiversion = os.environ["AZURE_OPENAI_API_VERSION"]
aoai_model_name = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
system_prompt = os.environ.get("SYSTEM_PROMPT", "You are an expert on Video Analysis. You will be shown a series of images from a video. Describe what is happening in the video, including the objects, actions, and any other relevant details. Be as specific and detailed as possible.")
print(f'aoai_endpoint: {aoai_endpoint}, aoai_model_name: {aoai_model_name}')
# Create AOAI client for answer generation
aoai_client = AzureOpenAI(
    azure_deployment=aoai_model_name,
    api_version=aoai_apiversion, #'2024-02-15-preview',
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
def process_video(video_path, seconds_per_frame=SECONDS_PER_FRAME, resize=RESIZE_OF_FRAMES, output_dir='', temperature = DEFAULT_TEMPERATURE):
    base64Frames = []

    # Prepare the video analysis
    video = cv2.VideoCapture(video_path)
    total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = video.get(cv2.CAP_PROP_FPS)
    frames_to_skip = int(fps * seconds_per_frame)
    curr_frame=0

    # Prepare to write the frames to disk
    if output_dir != '': # if we want to write the frame to disk
        output_dir = 'frames'
        os.makedirs(output_dir, exist_ok=True)
        frame_count = 1

    # Loop through the video and extract frames at specified sampling rate
    while curr_frame < total_frames - 1:
        video.set(cv2.CAP_PROP_POS_FRAMES, curr_frame)
        success, frame = video.read()
        if not success:
            break

        # Resize the frame to save tokens and get faster answer from the model. If resize==0 don't resize
        if resize != 0:
            height, width, _ = frame.shape
            frame = cv2.resize(frame, (width // resize, height // resize))

        _, buffer = cv2.imencode(".jpg", frame)

        # Save frame as JPG file
        if output_dir != '': # if we want to write the frame to disk
            frame_filename = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(video_path))[0]}_frame_{frame_count}.jpg")
            print(f'Saving frame {frame_filename}')
            with open(frame_filename, "wb") as f:
                f.write(buffer)            
            frame_count += 1

        base64Frames.append(base64.b64encode(buffer).decode("utf-8"))
        curr_frame += frames_to_skip
    video.release()
    print(f"Extracted {len(base64Frames)} frames")
    
    return base64Frames

# Function to transcript the audio from the local video with Whisper
def process_audio(video_path):

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
    print(f'SYSTEM PROMPT: [{system_prompt}]')
    print(f'USER PROMPT:   [{user_prompt}]')

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

    except Exception as ex:
        print(f'ERROR: {ex}')
        response = f'ERROR: {ex}'

    return response

# Split the video in segments of N seconds (by default 3 minutes). If segment_length is 0 the full video is processed
def split_video(video_path, output_dir, segment_length=180):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    
    if segment_length == 0: # Do not split
        segment_length = int(duration)

    for start_time in range(0, int(duration), segment_length):
        end_time = min(start_time + segment_length, duration)
        output_file = os.path.join(output_dir, f'{os.path.splitext(os.path.basename(video_path))[0]}_segment_{start_time}-{end_time}_secs.mp4')
        ffmpeg_extract_subclip(video_path, start_time, end_time, targetname=output_file)
        yield output_file

# Process the video
def execute_video_processing(st, segment_path, system_prompt, user_prompt, temperature):
    # Show the video on the screen
    st.write(f"Video: {segment_path}:")
    st.video(segment_path)

    with st.spinner(f"Analyzing video segment: {segment_path}"):
        # Extract 1 frame per second. Adjust the `seconds_per_frame` parameter to change the sampling rate
        with st.spinner(f"Extracting frames..."):
            start_time = time.time()
            if save_frames:
                output_dir = 'frames'
            else:
                output_dir = ''
            base64frames = process_video(segment_path, seconds_per_frame=seconds_per_frame, resize=resize, output_dir=output_dir, temperature=temperature)
            end_time = time.time()
            print(f'\t>>>> Frames extraction took {(end_time - start_time):.3f} seconds <<<<')
            ### st.write(f'Extracted {len(base64frames)} frames in {(end_time - start_time):.3f} seconds')

        # Extract the transcription of the audio
        if audio_transcription:
            msg = f'Analyzing frames and audio with {aoai_model_name}...'
            with st.spinner(f"Transcribing audio from video file..."):
                start_time = time.time()
                transcription = process_audio(segment_path)
                end_time = time.time()
            ### st.write(f'Transcription finished in {(end_time - start_time):.3f} seconds')
            print(f'Transcription: [{transcription}]')
            if show_transcription:
                st.markdown(f"**Transcription**: {transcription}", unsafe_allow_html=True)
            print(f'\t>>>> Audio transcription took {(end_time - start_time):.3f} seconds <<<<')
        else:
            msg = f'Analyzing frames with {aoai_model_name}...'
            transcription = ''
        # Analyze the video frames and the audio transcription with GPT-4o
        with st.spinner(msg):
            start_time = time.time()
            analysis = analyze_video(base64frames, system_prompt, user_prompt, transcription, temperature)
            end_time = time.time()
        print(f'\t>>>> Analysis with {aoai_model_name} took {(end_time - start_time):.3f} seconds <<<<')

    ### st.write(f"**Analysis of segment {segment_path}** ({(end_time - start_time):.3f} seconds)")
    end_time = time.time()
    print(f'\t>>>> {(end_time - start_time):.6f} seconds <<<<')
    st.success("Analysis completed.")

    return analysis

# Streamlit User Interface
st.set_page_config(
    page_title="Video Analysis with GPT-4o",
    layout="centered",
    initial_sidebar_state="auto",
)
st.image("microsoft.png", width=100)
st.title('Video Analysis with GPT-4o')

with st.sidebar:
    file_or_url = st.selectbox("Video source:", ["File", "URL"], index=0, help="Select the source, file or url")
    # file_or_url = "File"
    initial_split = 0
    if file_or_url == "URL":
        continuous_transmision = st.checkbox('Continuous transmission', False, help="Video of a continuous transmission")
        if continuous_transmision:
            initial_split = SEGMENT_DURATION
        
    audio_transcription = st.checkbox('Transcribe audio', True, help="Extract the audio transcription and use in the analysis or not")
    if audio_transcription:
        show_transcription = st.checkbox('Show audio transcription', True, help="Present the audio transcription or not")
    seconds_split = st.number_input('Number of seconds to split the video', initial_split, help="The video will be processed in smaller segments based on the number of seconds specified in this field. (0 to not split)")
    seconds_per_frame = float(st.text_input('Number of seconds per frame', SECONDS_PER_FRAME, help="The frames will be extracted every number of seconds specified in the field. It can be a decimal number, like 0.5, to extract a frame every half of second."))
    resize = st.number_input("Frames resizing ratio", 0, help="The size of the images will be reduced in proportion to this number while maintaining the height/width ratio. This reduction is useful for improving latency and reducing token consumption (0 to not resize)")
    save_frames = st.checkbox('Save the frames to the folder "frames"', False)
    temperature = float(st.number_input('Temperature for the model', DEFAULT_TEMPERATURE))
    system_prompt = st.text_area('System Prompt', system_prompt)
    user_prompt = st.text_area('User Prompt', USER_PROMPT)

# Prepare the segment directory
output_dir = "segments"
os.makedirs(output_dir, exist_ok=True)

# Prepare the video directory
video_dir = "video"
os.makedirs(video_dir, exist_ok=True)

# Video file or Video URL
if file_or_url == 'File':
    video_file = st.file_uploader("Upload a video file", type=["mp4", "avi", "mov"])
else:
    url = st.text_area("Enter the URL:", value='https://www.youtube.com/watch?v=Y6kHpAeIr4c', height=10)

# Analyze the video when the button is pressed
if st.button("Analyze video", use_container_width=True, type='primary'):

    # Show parameters:
    print(f"PARAMETERS:")
    print(f"file_or_url: {file_or_url}, audio_transcription: {audio_transcription}, seconds to split: {seconds_split}")
    print(f"seconds_per_frame: {seconds_per_frame}, resize ratio: {resize}, save_frames: {save_frames}, temperature: {temperature}")

    if file_or_url == 'URL': # Process Youtube video
        st.write(f'Analyzing video from URL {url}...')
        
        ydl_opts = {
                'format': '(bestvideo[vcodec^=av01]/bestvideo[vcodec^=vp9]/bestvideo)+bestaudio/best',
                # 'outtmpl': os.path.join(video_dir, 'full_video.%(ext)s'),
                'outtmpl': 'segment_%(start)s.mp4',
                'force_keyframes_at_cuts': True
        }
        ydl = yt_dlp.YoutubeDL(ydl_opts)
        if continuous_transmision == False:
            info_dict = ydl.extract_info(url, download=False)
            video_duration = int(info_dict.get('duration', 0))  # Convert to int

            if seconds_split == 0:
                segment_duration = video_duration
            else:
                segment_duration = int(seconds_split)  # Convert to int
        else:
            video_duration = int(48 * 60 * 60)  # Convert to int

            if seconds_split == 0:
                segment_duration = 180  # 3 minutes
            else:
                segment_duration = int(seconds_split)  # Convert to int

        for start in range(0, video_duration, segment_duration):
            end = start + segment_duration
            filename = f'segments/segment_{start}-{end}.mp4'
            with st.spinner(f"Downloading video from second {start} to {end}..."):
                # ydl_opts['outtmpl'] = filename
                # ydl_opts['download_ranges'] = [(start, end)]
                ydl_opts['outtmpl']['default'] = filename
                ydl_opts['download_ranges'] = download_range_func(None, [(start, end)])

                print(f'Updated ydl_opts: {ydl_opts}')
                print(f'start: {start}, video_duration: {video_duration}, segment_duration: {segment_duration}')
                try:
                    ydl.download([url])
                    print(f"Downloaded segment: {filename}")
                except Exception as e:
                    print(f"Error downloading segment: {e}")
                    break

            if os.path.exists(filename): # ext .mp4
                segment_path = filename
            else:
                segment_path = filename + '.mkv'
                if not os.path.exists(segment_path):
                    segment_path = filename + '.webm'

            print(f"Segment downloaded: {segment_path}")

            # Process the video segment
            analysis = execute_video_processing(st, segment_path, system_prompt, user_prompt, temperature)
            st.markdown(f"**Description**: {analysis}", unsafe_allow_html=True)
            #st.write(f"{analysis}")

            # Example detecting an event
            event="electric guitar"
            if event in analysis:
                st.write(f'**Detected event "{event}" in segment {segment_path}**')
            
            # Delete the video segment
            os.remove(segment_path)
            print(f"Deleted segment: {segment_path}")

    else: # Process the video file
        if video_file is not None:
            os.makedirs("temp", exist_ok=True)
            video_path = os.path.join("temp", video_file.name)
        try:
            with open(video_path, "wb") as f:
                f.write(video_file.getbuffer())
            print(f"Uploaded video file: {video_path}")

            # Splitting video in segment of N seconds (if seconds is 0 it will not split the video)
            for segment_path in split_video(video_path, output_dir, seconds_split):
                print(f"Processing segment: {segment_path}")
                # Process the video segment
                analysis = execute_video_processing(st, segment_path, system_prompt, user_prompt, temperature)
                st.write(f"{analysis}")

                # Delete the video segment
                os.remove(segment_path)
                print(f"Deleted segment: {segment_path}")

        except Exception as ex:
            print(f'ERROR: {ex}')
            st.write(f'ERROR: {ex}')