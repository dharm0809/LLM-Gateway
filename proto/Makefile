# Makefile for generating gRPC stubs from governance.proto
#
# Prerequisites:
#   Go stubs:
#     go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
#     go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
#     Ensure $(go env GOPATH)/bin is on your PATH.
#
#   Python stubs:
#     pip install 'walacor-gateway[grpc]'   (installs grpcio + grpcio-tools)
#     — or —
#     pip install grpcio-tools>=1.60
#
#   Both:
#     protoc (Protocol Buffers compiler) v3.21+ must be installed.
#     macOS:   brew install protobuf
#     Ubuntu:  apt install -y protobuf-compiler
#     Manual:  https://github.com/protocolbuffers/protobuf/releases

PROTO_DIR   := .
PROTO_FILE  := governance.proto

# Output directories (relative to repo root)
GO_OUT      := ../proxy/pb
PYTHON_OUT  := ../src/gateway/grpc

.PHONY: generate go python clean dirs

# Generate stubs for both Go and Python.
generate: dirs go python
	@echo "✓ gRPC stubs generated for Go ($(GO_OUT)) and Python ($(PYTHON_OUT))"

# Create output directories if they don't exist.
dirs:
	@mkdir -p $(GO_OUT)
	@mkdir -p $(PYTHON_OUT)

# Generate Go stubs (protobuf messages + gRPC service).
go:
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(GO_OUT) \
		--go_opt=paths=source_relative \
		--go-grpc_out=$(GO_OUT) \
		--go-grpc_opt=paths=source_relative \
		$(PROTO_FILE)

# Generate Python stubs (protobuf messages + gRPC service).
python:
	python -m grpc_tools.protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PYTHON_OUT) \
		--grpc_python_out=$(PYTHON_OUT) \
		--pyi_out=$(PYTHON_OUT) \
		$(PROTO_FILE)

# Remove generated files.
clean:
	rm -f $(GO_OUT)/governance.pb.go
	rm -f $(GO_OUT)/governance_grpc.pb.go
	rm -f $(PYTHON_OUT)/governance_pb2.py
	rm -f $(PYTHON_OUT)/governance_pb2_grpc.py
	rm -f $(PYTHON_OUT)/governance_pb2.pyi
