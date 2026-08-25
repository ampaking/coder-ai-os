import http from "node:http";

const port = Number(process.argv[2]);
let attempts = 0;

http.createServer((request, response) => {
  if (request.url === "/attempt") {
    attempts += 1;
    response.writeHead(204);
    response.end();
    return;
  }
  if (request.url === "/count") {
    response.writeHead(200, { "content-type": "text/plain" });
    response.end(String(attempts));
    return;
  }
  response.writeHead(200, { "content-type": "text/html" });
  if (request.url === "/dashboard") {
    response.end('<!doctype html><html><body><a id="logout">Logout</a></body></html>');
    return;
  }
  if (request.url === "/public") {
    response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width"></head><body>Public</body></html>');
    return;
  }
  response.end(`<!doctype html><html><body>
    <form><input name="email"><input name="password" type="password"><button type="submit">Login</button></form>
    <p id="error"></p>
    <script>
      if (localStorage.getItem("val-auth") === "ok") location.replace("/dashboard");
      document.querySelector("form").addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetch("/attempt", { method: "POST" });
        const email = document.querySelector('[name="email"]').value;
        const password = document.querySelector('[name="password"]').value;
        if (email === "test@example.invalid" && password === "fixture-password") {
          localStorage.setItem("val-auth", "ok");
          location.href = "/dashboard";
        } else document.querySelector("#error").textContent = "Invalid credentials";
      });
    </script>
  </body></html>`);
}).listen(port, "127.0.0.1");
