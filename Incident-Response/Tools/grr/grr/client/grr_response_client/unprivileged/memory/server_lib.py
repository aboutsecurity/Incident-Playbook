#!/usr/bin/env python
"""Unprivileged memory RPC server."""

import abc
import sys
import traceback
from typing import TypeVar, Generic, Optional, Tuple
import yara
from grr_response_client import client_utils
from grr_response_client.unprivileged import communication
from grr_response_client.unprivileged.proto import memory_pb2


class Error(Exception):
  """Base class for exceptions in this module."""
  pass


class DispatchError(Error):
  """Error while dispatching a request."""
  pass


class State:
  """State of the memory RPC server."""

  def __init__(self):
    self.yara_rules: Optional[yara.Rules] = None


class ConnectionWrapper:
  """Wraps a connection, adding protobuf serialization."""

  def __init__(self, connection: communication.Connection):
    self._connection = connection

  def Send(self, response: memory_pb2.Response) -> None:
    self._connection.Send(
        communication.Message(response.SerializeToString(), b""))

  def Recv(self) -> memory_pb2.Request:
    raw_request, _ = self._connection.Recv()
    request = memory_pb2.Request()
    request.ParseFromString(raw_request)
    return request


RequestType = TypeVar("RequestType")
ResponseType = TypeVar("ResponseType")


class OperationHandler(abc.ABC, Generic[RequestType, ResponseType]):
  """Base class for RPC handlers."""

  def __init__(self, state: State, request: memory_pb2.Request,
               connection: ConnectionWrapper):
    self._state = state
    self._request = request
    self._connection = connection

  def Run(self) -> None:
    request = self.UnpackRequest(self._request)
    response = self.HandleOperation(self._state, request)
    self._connection.Send(self.PackResponse(response))

  @abc.abstractmethod
  def HandleOperation(self, state: State, request: RequestType) -> ResponseType:
    """The actual implementation of the RPC."""
    pass

  @abc.abstractmethod
  def PackResponse(self, response: ResponseType) -> memory_pb2.Response:
    """Packs an inner Response message into a response RPC message."""
    pass

  @abc.abstractmethod
  def UnpackRequest(self, request: memory_pb2.Request) -> RequestType:
    """Extracts an inner Request message from a Request RPC message."""
    pass


class UploadSignatureHandler(
    OperationHandler[memory_pb2.UploadSignatureRequest,
                     memory_pb2.UploadSignatureResponse]):
  """Implements the UploadSignature operation."""

  def HandleOperation(
      self, state: State, request: memory_pb2.UploadSignatureRequest
  ) -> memory_pb2.UploadSignatureResponse:
    state.yara_rules = yara.compile(source=request.yara_signature)
    return memory_pb2.UploadSignatureResponse()

  def PackResponse(
      self,
      response: memory_pb2.UploadSignatureResponse) -> memory_pb2.Response:
    return memory_pb2.Response(upload_signature_response=response)

  def UnpackRequest(
      self, request: memory_pb2.Request) -> memory_pb2.UploadSignatureRequest:
    return request.upload_signature_request


def _YaraStringMatchToProto(
    offset: int, value: Tuple[int, str, bytes]) -> memory_pb2.StringMatch:
  return memory_pb2.StringMatch(
      offset=offset + value[0], string_id=value[1], data=value[2])


def _YaraMatchToProto(offset: int, value: "yara.Match") -> memory_pb2.RuleMatch:
  result = memory_pb2.RuleMatch(rule_name=value.rule)
  for yara_string_match in value.strings:
    result.string_matches.append(
        _YaraStringMatchToProto(offset, yara_string_match))
  return result


class ProcessScanHandler(OperationHandler[memory_pb2.ProcessScanRequest,
                                          memory_pb2.ProcessScanResponse]):
  """Implements the ProcessScan operation."""

  def HandleOperation(
      self, state: State,
      request: memory_pb2.ProcessScanRequest) -> memory_pb2.ProcessScanResponse:
    if state.yara_rules is None:
      raise Error("Rules have not been set.")
    with client_utils.CreateProcessFromSerializedFileDescriptor(
        request.serialized_file_descriptor) as process:
      result = memory_pb2.ScanResult()
      data = process.ReadBytes(request.offset, request.size)
      try:
        for yara_match in state.yara_rules.match(
            data=data, timeout=request.timeout_seconds):
          result.scan_match.append(
              _YaraMatchToProto(request.offset, yara_match))
        return memory_pb2.ProcessScanResponse(
            scan_result=result,
            status=memory_pb2.ProcessScanResponse.Status.NO_ERROR)
      except yara.TimeoutError as e:
        return memory_pb2.ProcessScanResponse(
            status=memory_pb2.ProcessScanResponse.Status.TIMEOUT_ERROR)
      except yara.Error as e:
        # Yara internal error 30 is too many hits.
        if "internal error: 30" in str(e):
          return memory_pb2.ProcessScanResponse(
              status=memory_pb2.ProcessScanResponse.Status.TOO_MANY_MATCHES)
        else:
          return memory_pb2.ProcessScanResponse(
              status=memory_pb2.ProcessScanResponse.Status.GENERIC_ERROR)

  def PackResponse(
      self, response: memory_pb2.ProcessScanResponse) -> memory_pb2.Response:
    return memory_pb2.Response(process_scan_response=response)

  def UnpackRequest(
      self, request: memory_pb2.Request) -> memory_pb2.ProcessScanRequest:
    return request.process_scan_request


def DispatchWrapped(connection: ConnectionWrapper) -> None:
  """Dispatches a request to the proper OperationHandler."""
  state = State()
  while True:
    try:
      request = connection.Recv()

      if request.HasField("upload_signature_request"):
        handler_class = UploadSignatureHandler
      elif request.HasField("process_scan_request"):
        handler_class = ProcessScanHandler
      else:
        raise DispatchError("No request set.")

      handler = handler_class(state, request, connection)
      handler.Run()
    except:  # pylint: disable=bare-except
      exception = memory_pb2.Exception(
          message=str(sys.exc_info()[1]),
          formatted_exception=traceback.format_exc())
      connection.Send(memory_pb2.Response(exception=exception))


def Dispatch(connection: communication.Connection) -> None:
  DispatchWrapped(ConnectionWrapper(connection))
