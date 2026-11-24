FROM eclipse-temurin:17-jdk-jammy@sha256:96ac8197b2cbc2bb0f9fbcbf91dbb0015f43b3c76a1cb351892da12e7bb91bed AS build
WORKDIR /src
COPY . .
RUN ./gradlew --no-daemon test bootJar

FROM eclipse-temurin:17-jdk-jammy@sha256:96ac8197b2cbc2bb0f9fbcbf91dbb0015f43b3c76a1cb351892da12e7bb91bed
WORKDIR /app
COPY --from=build /src/build/libs/*-SNAPSHOT.jar app.jar
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
