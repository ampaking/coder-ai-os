import http from "node:http";

const port = Number(process.argv[2]);
let backendCalls = 0;

http.createServer((request, response) => {
  if (request.url === "/count") {
    response.writeHead(200, { "content-type": "text/plain" });
    response.end(String(backendCalls));
    return;
  }
  if (request.url === "/api/profile") {
    backendCalls += 1;
    response.writeHead(500, { "content-type": "application/json" });
    response.end('{"role":"unexpected-live-backend"}');
    return;
  }
  response.writeHead(200, { "content-type": "text/html" });
  response.end(`<!doctype html><html lang="en"><head><title>Fixture dashboard</title><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>
    <main id="app">Loading</main>
    <script>
      if (localStorage.getItem("val-auth") !== "ok") location.replace("/login");
      else fetch("/api/profile").then((response) => response.json()).then((profile) => {
        document.querySelector("#app").innerHTML = profile.role === "fixture-admin"
          ? "<h1>Fixture Admin</h1>"
          : '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">';
      });
    </script>
  </body></html>`);
}).listen(port, "127.0.0.1");
