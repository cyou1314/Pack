import assert from "node:assert/strict";
import test from "node:test";
import {validateHubURL} from "../hub-url-policy.mjs";

test("remote Hub endpoints require HTTPS", () => {
  assert.equal(validateHubURL("https://hub.example.com/mcp"), "https://hub.example.com/mcp");
  assert.throws(() => validateHubURL("http://hub.example.com/mcp"), /invalid_hub_url/);
  assert.throws(() => validateHubURL("http://192.168.1.10:8794/mcp"), /invalid_hub_url/);
});

test("HTTP is restricted to explicit loopback hosts", () => {
  assert.equal(validateHubURL("http://127.0.0.1:8794/mcp"), "http://127.0.0.1:8794/mcp");
  assert.equal(validateHubURL("http://localhost:8794/mcp"), "http://localhost:8794/mcp");
  assert.equal(validateHubURL("http://[::1]:8794/mcp"), "http://[::1]:8794/mcp");
  assert.equal(validateHubURL("http://localhost.:8794/mcp"), "http://localhost.:8794/mcp");
});

test("Hub URLs reject credentials, query strings, fragments and other protocols", () => {
  for (const value of [
    "https://user:pass@hub.example.com/mcp",
    "https://hub.example.com/mcp?token=secret",
    "https://hub.example.com/mcp?",
    "https://hub.example.com/mcp#fragment",
    "https://hub.example.com/mcp#",
    "ftp://hub.example.com/mcp",
    " https://hub.example.com/mcp ",
  ]) {
    assert.throws(() => validateHubURL(value), /invalid_hub_url/, value);
  }
});
