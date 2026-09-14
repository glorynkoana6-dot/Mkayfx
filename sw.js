const CACHE = "mkayfx-v5-shell-1";

const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./mkayfx-icon.svg"
];


self.addEventListener(
  "install",
  event => {

    event.waitUntil(

      caches
        .open(CACHE)
        .then(
          cache =>
            cache.addAll(
              SHELL
            )
        )
        .then(
          () =>
            self.skipWaiting()
        )

    );

  }
);


self.addEventListener(
  "activate",
  event => {

    event.waitUntil(

      caches
        .keys()
        .then(
          keys =>
            Promise.all(

              keys
                .filter(
                  key =>
                    key !== CACHE
                )
                .map(
                  key =>
                    caches.delete(
                      key
                    )
                )

            )
        )
        .then(
          () =>
            self.clients.claim()
        )

    );

  }
);


self.addEventListener(
  "fetch",
  event => {

    const url =
      new URL(
        event.request.url
      );


    if(
      url.pathname.endsWith(
        "/data.json"
      )
      ||
      url.pathname.endsWith(
        "/signal_history.json"
      )
    ){

      event.respondWith(

        fetch(
          event.request
        )
        .catch(
          () =>
            caches.match(
              event.request
            )
        )

      );

      return;

    }


    event.respondWith(

      caches
        .match(
          event.request
        )
        .then(
          cached => {

            if(cached){

              return cached;

            }


            return fetch(
              event.request
            )
            .then(
              response => {

                const copy =
                  response.clone();


                caches
                  .open(CACHE)
                  .then(
                    cache =>
                      cache.put(
                        event.request,
                        copy
                      )
                  );


                return response;

              }
            );

          }
        )

    );

  }
);