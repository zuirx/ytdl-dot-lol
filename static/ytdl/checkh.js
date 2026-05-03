const app = document.getElementById("app");

const healthUrl = "https://m.ytdl.lol/health/";
const redirectUrl = "https://m.ytdl.lol/";
const fallbackUrl = "https://zuirx.github.io/ylol";

let checking = false;

async function checkOnline() {
  if (checking) return;
  checking = true;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);

  try {
    const res = await fetch(healthUrl + "?t=" + Date.now(), {
      method: "GET",
      cache: "no-store", // always bypass cache for health check
      signal: controller.signal
    });

    clearTimeout(timeout);
    checking = false;

    if (res.ok) {
      window.location.href = redirectUrl;
    } else {
      goToFallback();
    }
  } catch (err) {
    clearTimeout(timeout);
    checking = false;
    goToFallback();
  }
}

function goToFallback() {
  app.innerHTML = `
    <main style="font-family: Arial; text-align: center; margin-top: 15vh;">
      <h1>Redirecting...</h1>
      <p>Service unavailable. Sending you to fallback.</p>
    </main>
  `;

  setTimeout(() => {
    window.location.href = fallbackUrl;
  }, 1000);
}

checkOnline();