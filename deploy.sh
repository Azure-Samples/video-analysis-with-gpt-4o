#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status

# Check if the prefix is provided as an argument
if [ -z "$1" ]; then
    echo "Usage: $0 <prefix> [python_script]"
    exit 1
fi

# Variables
PREFIX=$1
PREFIX_LOWER=$(echo "$PREFIX" | tr '[:upper:]' '[:lower:]')

RESOURCE_GROUP="${PREFIX}ResourceGroup"
ACR_NAME="${PREFIX_LOWER}acr"  # Convert to lowercase
APP_SERVICE_PLAN="${PREFIX}AppServicePlan"
WEB_APP_NAME="${PREFIX_LOWER}app"  # Convert to lowercase
LOCATION="eastus"
IMAGE_NAME="${PREFIX_LOWER}-demo" # Convert to lowercase
DOCKER_IMAGE_TAG="latest"
PYTHON_SCRIPT=${2:-"video_shot_analysis.py"}  # Default to video_shot_analysis.py if not provided

echo "Starting deployment with the following parameters:"
echo "PREFIX: $PREFIX"
echo "RESOURCE_GROUP: $RESOURCE_GROUP"
echo "ACR_NAME: $ACR_NAME"
echo "APP_SERVICE_PLAN: $APP_SERVICE_PLAN"
echo "WEB_APP_NAME: $WEB_APP_NAME"
echo "LOCATION: $LOCATION"
echo "IMAGE_NAME: $IMAGE_NAME"
echo "DOCKER_IMAGE_TAG: $DOCKER_IMAGE_TAG"
echo "PYTHON_SCRIPT: $PYTHON_SCRIPT"

# Check if already logged in to Azure
if ! az account show &>/dev/null; then
    echo "Logging in to Azure..."
    az login
else
    echo "Already logged in to Azure."
fi

# Set the subscription
SUBSCRIPTION_ID="$(az account show --query 'id' -o tsv)"
echo "Using subscription ID: $SUBSCRIPTION_ID"
az account set --subscription "$SUBSCRIPTION_ID"

# Create a resource group
echo "Creating resource group: $RESOURCE_GROUP in $LOCATION"
az group create --name $RESOURCE_GROUP --location $LOCATION

# Create an Azure Container Registry
echo "Creating Azure Container Registry: $ACR_NAME"
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true

# Login to the Azure Container Registry
echo "Logging in to Azure Container Registry: $ACR_NAME"
az acr login --name $ACR_NAME

# Build the Docker image
echo "Building Docker image: $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG"
docker build --platform linux/amd64 --build-arg PYTHON_SCRIPT=$PYTHON_SCRIPT -t $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG .

# Push the Docker image to the Azure Container Registry
echo "Pushing Docker image to Azure Container Registry: $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG"
docker push $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG

# The P1V3 tier (Premium v3 - P1v3) provides more CPU and memory resources for production workloads
# Create an App Service plan
echo "Creating App Service plan: $APP_SERVICE_PLAN"
az appservice plan create --name $APP_SERVICE_PLAN --resource-group $RESOURCE_GROUP --sku P1V3 --is-linux

# Create a Web App for Containers
echo "Creating Web App for Containers: $WEB_APP_NAME"
az webapp create --resource-group $RESOURCE_GROUP --plan $APP_SERVICE_PLAN --name $WEB_APP_NAME --deployment-container-image-name $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG

# Configure the Web App to use the Azure Container Registry
echo "Configuring Web App to use Azure Container Registry: $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG"
az webapp config container set --name $WEB_APP_NAME --resource-group $RESOURCE_GROUP --container-image-name $ACR_NAME.azurecr.io/$IMAGE_NAME:$DOCKER_IMAGE_TAG --container-registry-url https://$ACR_NAME.azurecr.io

# Load environment variables from .env file
echo "Loading environment variables from .env file"
set -a
source .env
set +a

# Set environment variables in Azure Web App
echo "Setting environment variables in Azure Web App: $WEB_APP_NAME"
az webapp config appsettings set --resource-group $RESOURCE_GROUP --name $WEB_APP_NAME --settings \
    AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT \
    AZURE_OPENAI_API_KEY=$AZURE_OPENAI_API_KEY \
    AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION \
    AZURE_OPENAI_DEPLOYMENT_NAME=$AZURE_OPENAI_DEPLOYMENT_NAME \
    WHISPER_ENDPOINT=$WHISPER_ENDPOINT \
    WHISPER_API_KEY=$WHISPER_API_KEY \
    WHISPER_API_VERSION=$WHISPER_API_VERSION \
    WHISPER_DEPLOYMENT_NAME=$WHISPER_DEPLOYMENT_NAME \
    SYSTEM_PROMPT="$SYSTEM_PROMPT"

echo "Deployment completed. You can access your Streamlit app at https://$WEB_APP_NAME.azurewebsites.net"
