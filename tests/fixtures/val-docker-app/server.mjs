import http from "node:http";

http.createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html" });
  response.end("<!doctype html><html lang=\"en\"><title>VAL Docker fixture</title><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><body>ready</body></html>");
}).listen(8000, "0.0.0.0");

