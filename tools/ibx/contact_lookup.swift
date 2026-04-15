import Contacts
import Foundation

let store = CNContactStore()
let keysToFetch = [CNContactGivenNameKey, CNContactFamilyNameKey, CNContactPhoneNumbersKey] as [CNKeyDescriptor]
let args = Array(CommandLine.arguments.dropFirst())

if args.isEmpty {
    print("Usage: contact_lookup <phone_or_name> [phone_or_name2] ...")
    exit(1)
}

// Separate phone lookups from name lookups
var phoneTargets = Set<String>()
var nameTargets = [String]()

for arg in args {
    let digits = arg.filter { $0.isNumber }
    if digits.count >= 7 {
        phoneTargets.insert(String(digits.suffix(10)))
    } else {
        nameTargets.append(arg.lowercased())
    }
}

let request = CNContactFetchRequest(keysToFetch: keysToFetch)
do {
    try store.enumerateContacts(with: request) { contact, stop in
        let fullName = "\(contact.givenName) \(contact.familyName)"
        let fullNameLower = fullName.lowercased()

        // Check phone targets
        for pv in contact.phoneNumbers {
            let digits = pv.value.stringValue.filter { $0.isNumber }
            let last10 = String(digits.suffix(10))
            if phoneTargets.contains(last10) {
                print("\(pv.value.stringValue)|\(fullName)")
            }
        }

        // Check name targets
        for name in nameTargets {
            if fullNameLower.contains(name) || contact.givenName.lowercased().contains(name) || contact.familyName.lowercased().contains(name) {
                for pv in contact.phoneNumbers {
                    print("\(pv.value.stringValue)|\(fullName)")
                }
                break
            }
        }
    }
} catch {
    print("Error: \(error)")
}
