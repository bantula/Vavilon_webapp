/**
 * Shared Azure Blob Storage helper.
 *
 * Lazily builds the BlobServiceClient so a missing/invalid connection string
 * only fails the code path that actually needs Blob (guides / leads) instead of
 * crashing the whole backend at require-time.
 */
const { BlobServiceClient } = require('@azure/storage-blob');

let _service = null;

function getBlobService() {
  if (!_service) {
    const connStr = process.env.AZURE_STORAGE_CONNECTION_STRING;
    if (!connStr) throw new Error('AZURE_STORAGE_CONNECTION_STRING is not set');
    _service = BlobServiceClient.fromConnectionString(connStr);
  }
  return _service;
}

function getContainerClient(container) {
  return getBlobService().getContainerClient(container);
}

module.exports = { getContainerClient };
