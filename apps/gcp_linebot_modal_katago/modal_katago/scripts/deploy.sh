#!/bin/bash

# Modal KataGo Deployment Script
# This script automates the deployment of the Modal KataGo application

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODAL_KATAGO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODAL_APP_NAME="katago"
MODAL_SECRET_NAME="gcp-go-linebot"
MODAL_VOLUME_NAME="katago-models"
MODEL_FILENAME="kata1-b28c512nbt-s12192929536-d5655876072.bin.gz"

# Functions
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if Modal CLI is installed
check_modal_cli() {
    print_info "Checking Modal CLI installation..."
    if ! command -v modal &> /dev/null; then
        print_error "Modal CLI is not installed."
        echo "Please install it with: pip install modal"
        exit 1
    fi
    MODAL_VERSION=$(modal --version 2>/dev/null || echo "unknown")
    print_success "Modal CLI is installed (version: $MODAL_VERSION)"
}

# Check Modal authentication
check_modal_auth() {
    print_info "Checking Modal authentication..."
    
    # Check if Modal config file exists
    if [ -f "$HOME/.modal.toml" ]; then
        if grep -q "token_id" "$HOME/.modal.toml" && grep -q "token_secret" "$HOME/.modal.toml"; then
            print_success "Modal configuration found in ~/.modal.toml"
            
            # Verify token is valid by trying to list apps
            if modal app list &> /dev/null; then
                print_success "Modal authentication verified"
                return 0
            else
                print_warning "Config file exists but token may be invalid"
            fi
        fi
    fi
    
    # No valid authentication found
    print_error "Modal is not authenticated."
    echo ""
    echo "Please authenticate with: modal setup"
    echo ""
    echo "Alternative: Set environment variables:"
    echo "  export MODAL_TOKEN_ID=<your-token-id>"
    echo "  export MODAL_TOKEN_SECRET=<your-token-secret>"
    exit 1
}

# Check if Modal Secret exists
check_modal_secret() {
    print_info "Checking Modal Secret: $MODAL_SECRET_NAME..."
    if modal secret list 2>/dev/null | grep -q "$MODAL_SECRET_NAME"; then
        print_success "Modal Secret '$MODAL_SECRET_NAME' exists"
    else
        print_warning "Modal Secret '$MODAL_SECRET_NAME' not found"
        echo "Please create it with:"
        echo "  modal secret create $MODAL_SECRET_NAME \\"
        echo "    GCP_PROJECT_ID=your-project-id \\"
        echo "    GCS_BUCKET_NAME=your-bucket-name \\"
        echo "    GCP_SERVICE_ACCOUNT_KEY_JSON='{...}' \\"
        echo "    CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review"
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Check if model is uploaded to Volume
check_model_uploaded() {
    print_info "Checking if model is uploaded to Volume: $MODAL_VOLUME_NAME..."
    
    # Check if model file exists in the volume
    # modal volume ls <volume_name> [path] lists files in the volume
    if modal volume ls "$MODAL_VOLUME_NAME" 2>/dev/null | grep -q "$MODEL_FILENAME"; then
        print_success "Model is already uploaded to Volume"
        return 0
    else
        print_warning "Model not found in Volume"
        return 1
    fi
}

# Upload model to Volume
upload_model() {
    print_info "Uploading model to Modal Volume..."
    
    # Check if local model file exists
    LOCAL_MODEL_PATH="$MODAL_KATAGO_DIR/katago/models/$MODEL_FILENAME"
    if [ ! -f "$LOCAL_MODEL_PATH" ]; then
        print_error "Model file not found at: $LOCAL_MODEL_PATH"
        exit 1
    fi
    
    print_info "Model file found: $LOCAL_MODEL_PATH"
    print_info "Starting upload (this may take 5-10 minutes for large files)..."
    
    cd "$MODAL_KATAGO_DIR"
    if modal run main.py::upload_model; then
        print_success "Model uploaded successfully"
    else
        print_error "Failed to upload model"
        exit 1
    fi
}

# Deploy Modal application
deploy_app() {
    print_info "Deploying Modal application: $MODAL_APP_NAME..."
    
    cd "$MODAL_KATAGO_DIR"
    if modal deploy main.py; then
        print_success "Modal application deployed successfully"
    else
        print_error "Failed to deploy Modal application"
        exit 1
    fi
}

# Main deployment flow
main() {
    echo "=========================================="
    echo "  Modal KataGo Deployment Script"
    echo "=========================================="
    echo ""
    
    # Pre-flight checks
    check_modal_cli
    check_modal_auth
    check_modal_secret
    
    # Check and upload model if needed
    if ! check_model_uploaded; then
        echo ""
        read -p "Model not found in Volume. Upload now? (Y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            upload_model
        else
            print_warning "Skipping model upload. Make sure to upload it before using the application."
        fi
    fi
    
    echo ""
    print_info "Ready to deploy. Press Enter to continue or Ctrl+C to cancel..."
    read
    
    # Deploy application
    deploy_app
    
    echo ""
    echo "=========================================="
    print_success "Deployment completed!"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo "1. Verify deployment: modal app list"
    echo "2. Check logs: modal app logs $MODAL_APP_NAME"
    echo "3. Test the application through your LINE Bot"
    echo ""
}

# Run main function
main

