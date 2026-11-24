package project.smarthome.fcm;

import org.springframework.stereotype.Service;

@Service
public class FirebaseService {
    public void sendPushAll() {
        System.out.println("PiLab: Firebase notification observed");
    }
}
