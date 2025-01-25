# Use the official Python image from the Docker Hub
FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Install dependencies for OpenCV and other necessary libraries
RUN apt-get update && apt-get install -y \
    libgl1 \
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1

# Copy the requirements file into the container
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose the port that Streamlit will run on
EXPOSE 8501

# Use build argument to specify the Python script to run
ARG PYTHON_SCRIPT

# Set the default value for the Python script if not provided
ENV PYTHON_SCRIPT=${PYTHON_SCRIPT}

# Run the specified Streamlit app
CMD ["sh", "-c", "streamlit run ${PYTHON_SCRIPT}"]